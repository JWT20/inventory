# Mandatory Human Linking (No-Code Shipment Lines)

This document describes a stricter inbound flow for lines that do **not** contain a supplier code.

## Goal
Never infer or suggest a SKU for a no-code line. A warehouse user must select
the SKU before the line can be booked.

## Proposed flow

1. User uploads pakbon/factuur image to `/api/shipments/extract-preview`.
2. Vision extraction returns line rows (`supplier_code`, `description`, `quantity`, confidence).
3. For rows with `supplier_code`:
   - resolve only through `(supplier_name, supplier_code)` mappings.
4. For rows **without** `supplier_code`:
   - do not call an LLM matcher;
   - return the row with `match_source="unresolved"`,
     `candidate_matches=[]`, and `needs_confirmation=true`;
   - do not set `matched_sku_id`.
5. UI shows the normal SKU selector and article text.
6. User picks a SKU or marks the line as not to be booked.
7. Backend records confirmation event (`confirmed_by`, timestamp, chosen SKU, candidate set).
8. Optional: if user checks "save mapping", write a persistent supplier mapping rule.

## API shape (minimal extension)

For extracted lines, add fields:
- `needs_confirmation: bool`
- `candidate_matches: []` for no-code lines (kept for API compatibility)
- `match_source: "supplier_mapping" | "unresolved"`

Add a confirmation endpoint:
- `POST /api/shipments/confirm-line-match`
- body: `{supplier_name, supplier_code, chosen_sku_id, persist_mapping: bool}`

## Why this is safer

- Prevents inventory drift from guessed matches on ambiguous names/vintages.
- Keeps operator in control on exactly the cases where deterministic keys are missing.
- Creates an audit trail of why a no-code line was linked to a SKU.

## Operational behavior

- Fast path (supplier code exists): still deterministic and automatic.
- Risky path (no code): fully manual SKU selection.
- Over time, confirmations can be promoted into stable supplier mappings, reducing future manual work.
