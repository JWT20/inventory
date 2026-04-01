# LLM Suggest-Only + Mandatory Human Confirmation (No-Code Shipment Lines)

This document describes a stricter inbound flow for lines that do **not** contain a supplier code.

## Goal
Never auto-link a SKU from LLM output alone for no-code lines. LLM can suggest candidates, but a warehouse user must confirm before any mapping or booking is finalized.

## Proposed flow

1. User uploads pakbon/factuur image to `/api/shipments/extract-preview`.
2. Vision extraction returns line rows (`supplier_code`, `description`, `quantity`, confidence).
3. For rows with `supplier_code`:
   - resolve only through `(supplier_name, supplier_code)` mappings.
4. For rows **without** `supplier_code`:
   - call LLM matcher to return top candidates (SKU code + confidence + rationale).
   - mark row as `needs_confirmation=true`.
   - do **not** set `matched_sku_id` as final yet.
5. UI shows candidate list with confidence and article text snippet.
6. User picks one candidate or marks "unresolved".
7. Backend records confirmation event (`confirmed_by`, timestamp, chosen SKU, candidate set).
8. Optional: if user checks "save mapping", write a persistent supplier mapping rule.

## API shape (minimal extension)

For extracted lines, add fields:
- `needs_confirmation: bool`
- `candidate_matches: [{sku_id, sku_code, sku_name, confidence}]`
- `match_source: "supplier_mapping" | "llm_suggestion" | "unresolved"`
- `confirmed_sku_id: int | null`

Add a confirmation endpoint:
- `POST /shipments/{id}/confirm-line-match`
- body: `{line_index, chosen_sku_id, persist_mapping: bool}`

## Why this is safer

- Prevents inventory drift from LLM hallucinations on ambiguous names/vintages.
- Keeps operator in control on exactly the cases where deterministic keys are missing.
- Creates an audit trail of why a no-code line was linked to a SKU.

## Operational behavior

- Fast path (supplier code exists): still deterministic and automatic.
- Risky path (no code): guided human-in-the-loop workflow.
- Over time, confirmations can be promoted into stable supplier mappings, reducing future manual work.
