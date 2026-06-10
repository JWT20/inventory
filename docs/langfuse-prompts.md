# Langfuse-prompts (bron van waarheid)

De code bevat **geen** lokale prompt-fallbacks meer: elke prompt hieronder moet
in het Langfuse-dashboard bestaan vóór deploy van de bijbehorende release,
anders falen vision/extractie met `PromptUnavailableError` (503).
Zie docs/flessen.md, sectie I.

Benodigde prompts:

| Naam | Status |
|------|--------|
| `classify-and-describe` | herschreven (flessen toegestaan) — tekst hieronder |
| `describe-package` | herschreven (flessen toegestaan) — tekst hieronder |
| `classify` | **nieuw** (verving lokale constante) — tekst hieronder |
| `extract-shipment-document` | alleen de quantity-regel gewijzigd — tekst hieronder |
| `extract-shipment-text` | bestond al in Langfuse, ongewijzigd |
| `match-shipment-article-name` | inhoudelijk gelijk; alleen de lokale fallback is verwijderd — tekst hieronder ter referentie |

---

## `classify-and-describe`

```
Analyze this image and respond in EXACTLY this JSON format — no markdown fencing, no extra text:

{"is_package": true, "description": "detailed description here"}

CLASSIFICATION:
Set "is_package" to true if the image shows an identifiable retail product to match: any box, case, crate, carton or packaging, OR a single sealed, labelled bottle (e.g. a wine, cava or spirits bottle).
Set it to false for things that are not a matchable product unit: loose objects, scenes, furniture, electronics without packaging, food without packaging, a poured glass of wine, or an empty/unlabelled bottle.
If is_package is false, set "description" to a brief 5-word summary of what you see.

DESCRIPTION (only when is_package is true):
Your description will be embedded and compared against a reference database using cosine similarity.
Accuracy and specificity are critical — a wrong match means the wrong product gets shipped.

Transcribe ALL visible text exactly as printed (brand names, product names, years, volumes, certifications, codes).
Describe visual elements: dominant colors, logos, crests, illustrations, label placement, and the box material — or, for a bottle, the glass color, capsule/foil color, bottle shape and closure.
If this appears to be wine, pay special attention to: producer/domaine, wine name/cuvée, vintage year, appellation/region, classification.
For logos or symbols without readable text: describe the geometric structure (shapes, symmetry, line weight), position on the box or bottle label, relative size, and color contrast. Be precise about what the shapes depict.

ONLY describe what you can actually see. Do NOT mention things that are "not visible" or "not present" — simply omit them.

Format the description as a compact paragraph starting with the most distinctive identifiers, optimized for text-similarity search.
```

## `describe-package`

```
Describe this product — a box/case or a single labelled bottle — for identification matching.
Your description will be embedded and compared against a reference database using cosine similarity.
Accuracy and specificity are critical — a wrong match means the wrong product gets shipped.

Transcribe ALL visible text exactly as printed (brand names, product names, years, volumes, certifications, codes).
Describe visual elements: dominant colors, logos, crests, illustrations, label placement, and the box material — or, for a bottle, the glass color, capsule/foil color, bottle shape and closure.
If this appears to be wine, pay special attention to: producer/domaine, wine name/cuvée, vintage year, appellation/region, classification.
For logos or symbols without readable text: describe the geometric structure (shapes, symmetry, line weight), position on the box or bottle label, relative size, and color contrast. Be precise about what the shapes depict.

ONLY describe what you can actually see. Do NOT mention things that are "not visible" or "not present" — simply omit them.

Format as a compact paragraph starting with the most distinctive identifiers, optimized for text-similarity search.
```

## `classify` (nieuw)

```
Analyze this image and respond in EXACTLY this JSON format — no markdown fencing, no extra text:

{"is_package": true, "summary": "brief 5-word description"}

Set "is_package" to true if the image shows an identifiable retail product to match: any box, case, crate, carton or packaging, OR a single sealed, labelled bottle (e.g. a wine, cava or spirits bottle).
Set it to false for loose objects, scenes, furniture, electronics without packaging, food without packaging, a poured glass of wine, or an empty/unlabelled bottle.

Examples of true: wine box, shoe box, cardboard carton, wooden crate, sealed package, shipping parcel, a labelled wine or cava bottle.
Examples of false: a clock, candles on a table, a laptop, a pair of shoes, a glass of wine.
```

## `extract-shipment-document`

Alleen de eerste quantity-regel is gewijzigd t.o.v. de oude lokale fallback
(de backend rekent niet langer zelf met een vaste 6-per-doos aanname op
promptniveau; de eenheid volgt het gematchte product):

```
You are a delivery-note and invoice analysis agent for an inbound warehouse receiving system.
You will receive a single document image (pakbon, factuur, or similar).
Extract all product lines visible on the document.
Output MUST be valid JSON matching the structure below exactly.

JSON structure:
{
  "supplier_name": "string",
  "reference": "string",
  "document_type": "pakbon|invoice|unknown",
  "raw_text": "short transcription summary",
  "lines": [
    {
      "supplier_code": "string",
      "description": "string",
      "evidence": {
        "line_text": "raw line text",
        "quantity_text": "raw quantity fragment",
        "unit_hint": "column header or inline label that identifies the unit"
      },
      "quantity": 102,
      "quantity_unit": "pieces",
      "confidence": 0.91
    }
  ]
}

Quantity rules (IMPORTANT):
- Return ONE numeric quantity per line plus its unit. The backend converts the quantity to the product's order unit based on the matched product, so you MUST NOT do any box/bottle math yourself. Report the number and unit exactly as the document states.
- quantity_unit MUST be one of: "boxes" (dozen/colli/kisten/ds/ct), "pieces" (flessen/fl/btls/stuks/pcs), or "unknown".
- Decide the unit from document context in this priority:
  1. Column header directly above the number (e.g. 'Aantal', 'Colli', 'Dozen', 'Flessen', 'Fl', 'Btls').
  2. Inline label right next to the number (e.g. '18 fl', '3 ds', '2 colli').
  3. If the same line shows BOTH a small number (typically 1–5) and a larger number (e.g. 12, 24, 102) without explicit labels, the small number is boxes and the larger one is pieces — return the pieces value with quantity_unit='pieces'.
- If you truly cannot tell whether the number is boxes or pieces, set quantity_unit='unknown' and lower the confidence score. Do NOT guess.
- quantity MUST be a non-negative integer.
- Transcribe the raw fragment you used into evidence.quantity_text and the header/label you relied on into evidence.unit_hint.

Evidence rules:
- Keep evidence fields as short verbatim snippets from the document.

Filtering rules:
- Include only product lines.
- Ignore totals, pallet costs, transport, and signature fields.
- If uncertain about a line, include it with a lower confidence score.

Examples:
- "ART123 Merlot 6x75cl 18 fl" → quantity=18, quantity_unit="pieces", evidence.quantity_text="18 fl", evidence.unit_hint="fl".
- "ART456 Chardonnay 3 ds" → quantity=3, quantity_unit="boxes", evidence.quantity_text="3 ds", evidence.unit_hint="ds".
- "AFI810125 - Trent, VdD Pinot Grigio25 1 102 132,60 76,50" with column headers (Colli | Flessen | Brutto | Netto) → quantity=102, quantity_unit="pieces", evidence.quantity_text="102", evidence.unit_hint="Flessen".
- Single bare number with no header or label → quantity=<n>, quantity_unit="unknown", confidence lowered.
```

## `match-shipment-article-name`

Inhoudelijk ongewijzigd; ter referentie de tekst die voorheen als lokale
fallback in de code stond:

```
You are matching one inbound shipment line to an internal SKU catalog.
Return ONLY valid JSON:
{
  "sku_code": "string",
  "confidence": 0.0
}

Rules:
- Use the line description and optional supplier name.
- Choose from provided candidates only.
- If uncertain, return {"sku_code": "", "confidence": 0.0}.
```
