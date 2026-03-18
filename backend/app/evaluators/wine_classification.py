"""LLM-as-a-judge evaluator prompt for the wine classification pipeline.

This prompt is designed to be used as a Langfuse evaluator.
It covers ALL scenarios in the codebase, not just is_package classification.

Managed here so it can be uploaded to Langfuse as an evaluator config,
and iterated on alongside the code.
"""

# ---------------------------------------------------------------------------
# The evaluator prompt — covers every outcome in the pipeline
# ---------------------------------------------------------------------------

WINE_CLASSIFICATION_JUDGE_PROMPT = """\
You are evaluating a vision model that classifies images and describes wine boxes for product identification in a warehouse.

The model was given this prompt:
{{input}}

The model produced this output:
{{output}}

STEP 1 — Parse the output JSON. It should have this structure:
  {"is_package": true/false, "description": "..."}

If the JSON is malformed or missing fields, score 0.0.

STEP 2 — Determine the scenario and score accordingly:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCENARIO A: is_package is FALSE (model rejected the image)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The model rejected the image as not a box. This triggers an HTTP 422 in the
booking flow and returns null in the identify flow. Evaluate:

Score 1.0 — CORRECT REJECTION:
  - The image clearly does NOT show a box/case/crate/carton/packaging
  - The description/summary is a reasonable brief description of what IS visible

Score 0.5 — UNCERTAIN REJECTION:
  - The image is ambiguous (e.g. partially visible packaging, inner packaging
    without outer box, pallet-wrapped items)
  - OR the summary is empty/nonsensical but the rejection seems right

Score 0.0 — FALSE REJECTION:
  - The image DOES show a wine box/case/crate or product packaging
  - The model should have accepted it but incorrectly rejected it
  - This is the worst outcome: a valid product scan gets blocked

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCENARIO B: is_package is TRUE and image IS a wine/product box
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The model accepted the image. The description will be embedded and matched
against reference images via cosine similarity (threshold 0.80). A bad
description means no match (HTTP 404) or a wrong match. Evaluate on these
criteria:

1. TEXT TRANSCRIPTION (most important for matching):
   - Did it transcribe ALL visible text? (brand, producer, wine name, vintage,
     volume, appellation, classification, certifications)
   - Is the text transcribed EXACTLY as printed (not paraphrased)?
   - Are numbers correct (vintage year, volume)?

2. VISUAL DESCRIPTION (important for disambiguation):
   - Colors, logos, crests, illustrations, label design, box material
   - Logo geometry described precisely enough to distinguish from similar ones
   - Label placement and layout

3. SPECIFICITY (critical for correct matching):
   - Is the description specific enough to distinguish this wine from similar
     wines by the same producer?
   - Does it start with the most distinctive identifiers (optimized for
     embedding similarity)?

4. ACCURACY (no hallucination):
   - Does it ONLY describe what's visible?
   - Does it avoid mentioning things that are "not visible" or "not present"?
   - No invented details, brands, or years

Scoring:
  1.0 — All visible text transcribed exactly, visuals accurately described,
         specific enough for unambiguous matching, no hallucination
  0.8 — Minor omission (e.g. missing a certification code) but all key
         identifiers present (producer, wine name, vintage)
  0.6 — Key identifier missing (e.g. vintage year or producer) but still
         likely to match correctly
  0.4 — Multiple key identifiers missing, match will depend on visual
         similarity alone — may trigger human confirmation
  0.2 — Description is too vague for reliable matching (e.g. "red wine box
         with gold label") — will likely result in no match or wrong match
  0.0 — Description is hallucinated, completely wrong, or empty

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCENARIO C: is_package is TRUE but image is NOT actually a box/packaging
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This is a FALSE ACCEPTANCE — the model should have rejected the image but
didn't. This is problematic because:
  - The system will generate an embedding and attempt matching
  - It may return "no match found" (confusing for the warehouse worker)
  - Worse: it may coincidentally match a real product (wrong booking!)

Score 0.0 — always. A non-package image accepted as a package is always wrong,
regardless of how "good" the description text is.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY a JSON object with this exact format:
{"score": <float 0.0-1.0>, "reason": "<1-2 sentence explanation>"}
"""
