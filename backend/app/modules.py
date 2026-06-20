"""Canonical catalog of organization feature modules — the per-tenant scope flags.

A module on an :class:`~app.models.Organization` decides whether a flow may
*exist* for that merchant (e.g. the weekly approval flow, or photo-based
picking). Which flow actually runs for a given product/order is a separate
concern, driven by ``SKU.product_type`` — see ``PICKING_MODULE_BY_PRODUCT_TYPE``.

This module is the single source of truth: schema validation, the admin catalog
endpoint and the backfill migration all read from here, so the backend and the
frontend can never drift on which modules exist or what they are called.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleDef:
    key: str
    label: str
    description: str
    # Baseline modules are always on and cannot be turned off by the admin.
    baseline: bool = False


# Order here is the canonical display order used by the admin UI.
MODULE_DEFS: tuple[ModuleDef, ...] = (
    ModuleDef("inventory", "Voorraad", "Voorraadbeheer en -overzicht.", baseline=True),
    ModuleDef("orders", "Orders", "Orders aanmaken en verwerken.", baseline=True),
    ModuleDef(
        "barcode_picking",
        "Barcode/EAN picken",
        "Producten picken door de EAN te scannen met een handscanner.",
    ),
    ModuleDef(
        "channel_orders",
        "Bol/Shopify-orders",
        "Orders die via een verkoopkanaal (bol, Shopify) binnenkomen.",
    ),
    ModuleDef(
        "vision_picking",
        "Foto + AI-herkenning",
        "Producten herkennen via referentiefoto's en AI (de wijn-flow).",
    ),
    ModuleDef(
        "customer_portal",
        "Klant-accounts",
        "Klanten met eigen inlog + bestelscherm, plus klantbeheer.",
    ),
    ModuleDef(
        "week_overview",
        "Weekoverzicht + goedkeuring",
        "Wekelijkse orderplanning met goedkeuringsstap.",
    ),
    ModuleDef(
        "suppliers",
        "Leveranciers",
        "Leveranciers en inkomende pakbonnen.",
    ),
)

VALID_MODULES: tuple[str, ...] = tuple(m.key for m in MODULE_DEFS)
BASELINE_MODULES: tuple[str, ...] = tuple(m.key for m in MODULE_DEFS if m.baseline)

# Default for a brand-new organization: the barcode-fulfilment baseline. Vision
# is the exception (one merchant), so it is never on by default.
DEFAULT_MODULES: list[str] = ["inventory", "orders", "barcode_picking", "channel_orders"]

# A product's identification method maps 1-to-1 onto the module that must be
# enabled for its organization. ``product_type`` is the existing field (PR #353);
# there is deliberately no separate ``pick_method`` column.
PICKING_MODULE_BY_PRODUCT_TYPE: dict[str, str] = {
    "vision": "vision_picking",
    "barcode": "barcode_picking",
}


def normalize_modules(modules: list[str]) -> list[str]:
    """Validate a requested module list and guarantee the baseline.

    Raises :class:`ValueError` if any module is unknown. Baseline modules are
    always added. The result is deduplicated and returned in catalog order so
    storage is stable regardless of input ordering.
    """
    requested = set(modules)
    unknown = requested - set(VALID_MODULES)
    if unknown:
        raise ValueError(f"Onbekende module(s): {', '.join(sorted(unknown))}")
    requested |= set(BASELINE_MODULES)
    return [m for m in VALID_MODULES if m in requested]
