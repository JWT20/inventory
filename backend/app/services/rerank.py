"""Visual rerank: a second pass that looks at the photos, not the words.

The vector search in ``matching.py`` compares *text descriptions* of photos.
That works well across a catalogue of visually different products and fails
exactly where it hurts most: a product line whose boxes are near-identical and
differ in one printed word (a cultivar, a cuvée, a colour name). Their
descriptions come out near-identical, their embeddings come out near-identical,
and the ordering inside that cluster is noise — so the nearest neighbour can be
a box the picker never scanned.

This module takes the top candidates from that search and puts the scan photo
next to their reference photos in a single vision call: "which of these is the
same product, and what distinguishes it?". That is the comparison a picker makes
by eye and the one an embedding of a paraphrase cannot make.

The verdict is advisory, never silently authoritative: when the pass does not
run (disabled, no reference photos, API failure) or comes back unsure, callers
are expected to fall back to asking the human rather than to booking on the
vector guess alone.
"""

import asyncio
import json
import logging
import string

from dataclasses import dataclass, field

from google.genai import types
from langfuse import observe
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ReferenceImage, SKU
from app.services.embedding import _call_vision_multi, optimize_for_vision
from app.services.langfuse_client import get_prompt_required
from app.services.storage import storage

logger = logging.getLogger(__name__)

# Label handed to the model for "none of these". Kept out of the A/B/C space so
# a truncated or sloppy answer can never be mistaken for a real candidate.
NO_MATCH_LABEL = "NONE"

# Skip reason for "the vector search was already decisive". Distinct from every
# other skip reason because it is not a degradation: nothing was lost by not
# looking, so the picker gets a clean match instead of a warning.
NOT_NEEDED = "clear match"

_SCAN_LABEL = "SCAN"
_CANDIDATE_LABELS = string.ascii_uppercase  # A, B, C, …

_RERANK_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "choice": types.Schema(type=types.Type.STRING),
        "certainty": types.Schema(
            type=types.Type.STRING, enum=["high", "low"]
        ),
        "distinguishing_feature": types.Schema(type=types.Type.STRING),
    },
    required=["choice", "certainty", "distinguishing_feature"],
)


@dataclass(frozen=True)
class RerankCandidate:
    """One SKU offered to the rerank pass, with its reference photo keys."""

    sku_id: int
    sku_code: str
    sku_name: str
    similarity: float
    image_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RerankVerdict:
    """Outcome of the visual comparison.

    ``ran`` is False when the pass was skipped or failed — callers must treat
    that as "no opinion" (ask the human), not as agreement with the vector
    ranking. ``sku_id`` is None when the model saw the scan and recognised none
    of the candidates, which is the signal that the picker is holding a box that
    is not among them at all.
    """

    ran: bool
    sku_id: int | None = None
    certainty: str = "low"
    distinguishing_feature: str = ""
    considered_sku_ids: list[int] = field(default_factory=list)
    skip_reason: str = ""

    @property
    def is_confident(self) -> bool:
        return self.ran and self.sku_id is not None and self.certainty == "high"

    @property
    def rejected_all(self) -> bool:
        """The model looked at the candidates and recognised none of them."""
        return self.ran and self.sku_id is None

    @property
    def degraded(self) -> bool:
        """The visual check was wanted but could not be made.

        Distinguishes "we deliberately did not look, the vector search was
        unambiguous" from "we wanted to look and could not" — only the latter
        is a reason to bother the picker.
        """
        return not self.ran and self.skip_reason != NOT_NEEDED


def needs_visual_check(
    matches: list[tuple[SKU, float, str | None, str | None]],
    scope_sku_ids: set[int] | None = None,
) -> tuple[bool, str]:
    """Decide whether this scan is a close call worth a second, visual look.

    The rerank is the expensive step, and on a catalogue of visually distinct
    products the vector search is already right — spending a vision call to
    confirm a match that leads its runner-up by a wide margin buys nothing.

    Two situations do warrant it:

    * **Rivals inside the ambiguity margin.** That cluster is what a line of
      lookalike packaging looks like, and it is exactly where the ranking
      between the members is noise rather than signal.
    * **The best match in the whole catalogue is not open here.** Either the
      picker grabbed the wrong box, or a lookalike outranked the right one.
      Both need eyes before anything is proposed for booking.

    Returns ``(needed, reason)``; the reason doubles as the verdict's skip
    reason when nothing is needed.
    """
    if not matches:
        return False, NOT_NEEDED

    best_sku, best_similarity = matches[0][0], matches[0][1]

    if len(matches) >= 2 and best_similarity - matches[1][1] < settings.ambiguity_margin:
        return True, "rivals within the ambiguity margin"

    if scope_sku_ids is not None and best_sku.id not in scope_sku_ids:
        return True, "best catalogue match is not open in scope"

    return False, NOT_NEEDED


def select_rerank_candidates(
    db: Session,
    matches: list[tuple[SKU, float, str | None, str | None]],
    scope_sku_ids: set[int] | None = None,
) -> list[RerankCandidate]:
    """Narrow vector hits to the set worth comparing visually.

    The best hits inside the normal similarity band are kept first. The strongest
    open-order hit is then guaranteed one place, replacing the weakest selected
    hit when a large out-of-scope lookalike cluster would otherwise fill every
    slot. This keeps the closest catalogue rivals visible while ensuring the
    product that can actually be booked never disappears. The total stays
    bounded by ``rerank_max_candidates``.

    The best-matching reference image of each SKU is listed first, then its other
    usable photos up to ``rerank_images_per_sku``: different angles of the same
    box are what let the model find the one printed word that separates two
    variants.
    """
    if not matches:
        return []

    best_similarity = matches[0][1]
    cutoff = best_similarity - settings.rerank_similarity_band
    max_candidates = max(1, settings.rerank_max_candidates)

    plausible = [match for match in matches if match[1] >= cutoff]
    selected = plausible[:max_candidates]

    # Reserve one place for the strongest SKU that is actually open. Usually it
    # is already selected; the replacement path handles an out-of-scope cluster
    # that occupied every global candidate slot.
    if scope_sku_ids:
        best_in_scope = next(
            (match for match in matches if match[0].id in scope_sku_ids), None
        )
        selected_sku_ids = {match[0].id for match in selected}
        if (
            best_in_scope is not None
            and best_in_scope[0].id not in selected_sku_ids
        ):
            if len(selected) < max_candidates:
                selected.append(best_in_scope)
            else:
                selected[-1] = best_in_scope

    candidates: list[RerankCandidate] = []
    for sku, similarity, image_path, _ref_desc in selected:
        candidates.append(
            RerankCandidate(
                sku_id=sku.id,
                sku_code=sku.sku_code,
                sku_name=sku.name,
                similarity=similarity,
                image_keys=_reference_image_keys(db, sku.id, preferred=image_path),
            )
        )
    return [c for c in candidates if c.image_keys]


def _reference_image_keys(
    db: Session, sku_id: int, *, preferred: str | None
) -> list[str]:
    """Storage keys of a SKU's usable reference photos, best match first."""
    rows = (
        db.query(ReferenceImage.image_path)
        .filter(
            ReferenceImage.sku_id == sku_id,
            ReferenceImage.processing_status == "done",
            ReferenceImage.image_path.isnot(None),
        )
        .order_by(ReferenceImage.created_at)
        .all()
    )
    keys = [row[0] for row in rows if row[0]]
    if preferred and preferred in keys:
        keys.remove(preferred)
        keys.insert(0, preferred)
    elif preferred:
        keys.insert(0, preferred)
    return keys[: max(1, settings.rerank_images_per_sku)]


def _load_images(keys: list[str]) -> list:
    """Read and downscale reference photos; unreadable keys are skipped."""
    images = []
    for key in keys:
        data = storage.read(key)
        if data is None:
            logger.warning("Rerank: reference image %s could not be read", key)
            continue
        try:
            images.append(optimize_for_vision(data))
        except Exception:
            logger.warning("Rerank: reference image %s could not be decoded", key)
    return images


def _parse_verdict(
    raw: str, label_to_sku: dict[str, int], considered: list[int]
) -> RerankVerdict:
    data = json.loads(raw)
    choice = str(data.get("choice", "")).strip().upper()
    certainty = str(data.get("certainty", "low")).strip().lower()
    feature = str(data.get("distinguishing_feature", "")).strip()

    if certainty not in ("high", "low"):
        certainty = "low"

    if choice == NO_MATCH_LABEL:
        return RerankVerdict(
            ran=True,
            sku_id=None,
            certainty=certainty,
            distinguishing_feature=feature,
            considered_sku_ids=considered,
        )

    sku_id = label_to_sku.get(choice)
    if sku_id is None:
        # An answer outside the offered labels is not a usable opinion.
        raise ValueError(f"rerank returned unknown choice {choice!r}")

    return RerankVerdict(
        ran=True,
        sku_id=sku_id,
        certainty=certainty,
        distinguishing_feature=feature,
        considered_sku_ids=considered,
    )


@observe()
async def rerank_scan(
    scan_bytes: bytes, candidates: list[RerankCandidate]
) -> RerankVerdict:
    """Compare the scan photo against the candidates' reference photos.

    Returns a verdict that never raises: any failure degrades to ``ran=False``
    so a Gemini outage cannot block picking. Callers decide what "no opinion"
    means for their flow — for booking it means ask the picker, never auto-book.
    """
    if not settings.rerank_enabled:
        return RerankVerdict(ran=False, skip_reason="disabled")
    if not candidates:
        return RerankVerdict(ran=False, skip_reason="no candidates")
    if len(candidates) > len(_CANDIDATE_LABELS):
        candidates = candidates[: len(_CANDIDATE_LABELS)]

    try:
        scan_image = await asyncio.to_thread(optimize_for_vision, scan_bytes)

        images: list[tuple[str, object]] = [(_SCAN_LABEL, scan_image)]
        label_to_sku: dict[str, int] = {}
        roster: list[str] = []
        for index, candidate in enumerate(candidates):
            label = _CANDIDATE_LABELS[index]
            loaded = await asyncio.to_thread(_load_images, candidate.image_keys)
            if not loaded:
                continue
            label_to_sku[label] = candidate.sku_id
            roster.append(f"{label} = {candidate.sku_code} ({candidate.sku_name})")
            for image in loaded:
                images.append((label, image))

        if not label_to_sku:
            return RerankVerdict(ran=False, skip_reason="no readable reference images")

        considered = list(label_to_sku.values())
        prompt = "\n".join([
            get_prompt_required("rerank-candidates"),
            "",
            f"The scanned photo is labelled [{_SCAN_LABEL}].",
            "The candidate products and their reference photos are:",
            *roster,
            "",
            f'Answer with "choice": one of '
            f'{", ".join(sorted(label_to_sku))} — or "{NO_MATCH_LABEL}" if the '
            "scanned product is none of them.",
        ])

        raw = await _call_vision_multi(
            images, prompt, response_schema=_RERANK_SCHEMA
        )
        verdict = _parse_verdict(raw, label_to_sku, considered)
        logger.info(
            "Rerank verdict: sku_id=%s certainty=%s feature=%r (considered %d)",
            verdict.sku_id, verdict.certainty,
            verdict.distinguishing_feature[:120], len(considered),
        )
        return verdict
    except Exception as exc:
        # Advisory layer: never turn a rerank failure into a failed scan.
        logger.warning("Rerank pass failed, falling back to no opinion", exc_info=True)
        return RerankVerdict(ran=False, skip_reason=f"error: {type(exc).__name__}")
