# Plan — Fles-producten in WijnPick

Dit document is de overkoepelende kapstok voor het mogelijk maken van **losse
flessen / fles-producten** in het systeem. Het bewaakt de samenhang tussen de
losse PR's (zie [PR-strategie](#pr-strategie)) zodat de scope niet verschuift.

## Aanleiding

Sommige producten worden niet per doos verkocht maar als losse fles, bijv.
"Cava 0,0" die als 2 flessen i.p.v. een hele doos wordt besteld. Daarnaast zijn
er situaties waarin bepaalde wijnen alleen als losse fles bestelbaar zijn.

Het hele systeem gaat nu uit van **1 SKU = 1 doos** en **1 scan = 1 doos =
1 boeking**. We voegen een producteigenschap toe die een product als *fles*
markeert, en laten die door het systeem doorwerken. In dit ontwerp is een fles
gewoon zijn **eigen besteleenheid**: 1 fles = 1 besteleenheid = 1 scan =
1 boeking, met de prijs per fles. We rekenen flessen dus **niet** om naar dozen.

## Genomen beslissingen

1. **`is_bottle`** = boolean op SKU-niveau (default `false` = doos), werkt door
   het hele systeem.
2. Orderscherm krijgt een **aparte sectie "Flessen"**.
3. Ontvangst van flessen gaat via een **foto van de fles** (scannen met
   flesreferenties), niet via handmatig afvinken.
4. Rapportage toont dozen en flessen **apart** ("X dozen · Y flessen").
5. Prompts leven **alleen in Langfuse** — geen lokale fallback in de code.
6. Analytics-events: **open beslissing** (zie [Open beslissingen](#open-beslissingen)).

---

## A. Datamodel & migratie

- `models.py` → `SKU`: kolom
  ```python
  is_bottle: Mapped[bool] = mapped_column(
      Boolean, default=False, server_default=text("false"), nullable=False
  )
  ```
- Migratie `backend/alembic/versions/029_sku_is_bottle.py` (hoogste nu = 028):
  `add_column` met `server_default=false` → bestaande producten blijven doos.

## B. Backend API

- `schemas.py`: `is_bottle: bool = False` op `SKUCreate`;
  `is_bottle: bool | None = None` op `SKUUpdate`; `is_bottle: bool` op
  `SKUResponse`.
- `routers/skus.py`: `create_sku` (rond `:409`) zet `is_bottle=data.is_bottle`;
  `update_sku` (rond `:453`) volgt het bestaande `changed_fields`-patroon (zoals
  `supplier_id`).

## C. SKU-beheer UI

- `components/skus.tsx`: toggle **"Dit product is een losse fles"** in het
  aanmaak-/bewerkformulier.

## D. Eenheidslabels (fles/flessen i.p.v. doos/dozen)

- Helper `unitLabel(sku, n)` → `fles/flessen` als `is_bottle`, anders
  `doos/dozen`. Toepassen in:
  - `orders.tsx` (`:443`, `:500`, `:1312-1313`)
  - `weekly-summary.tsx`
  - `inventory.tsx`
  - **`receive.tsx`** (koeriers-scanscherm): `:305` "dozen geboekt", `:745`
    "Richt de camera op de doos", `:1030` "Volgende doos scannen", `:1094`
    "welke doos is dit", `:1150` "Hoeveel dozen van dit product?", `:1280` "Is
    dit dezelfde doos?".
- `"p/st"` blijft kloppen.

## E. Orderscherm: aparte "Flessen"-sectie

- In het order-aanmaakpaneel (`orders.tsx:933-967`) `currentLines` splitsen op
  `is_bottle` in twee gegroepeerde subsecties met kop **Dozen** en **Flessen**
  (zelfde checkbox + `OrderQuantityControl`).
- In order-detail/overzicht (rond `:1300`) fles-regels een badge **"Fles"**
  geven.
- **Order-aggregaten splitsen** (zie ook H): `total_boxes` /
  `booked_boxes` (`orders.py:175-176`) sommen nu *alle* regels op, ongeacht
  eenheid. Een gemengd order (flessen + dozen) wordt daardoor fout opgeteld en
  gelabeld in de orderlijst/kaarten (`orders.tsx:443,500,619,648,672`).
  Oplossing: order-niveau splitsen in een dozen-totaal en een flessen-totaal
  (of als gemengd markeren).

## F. Ontvangen/scannen via flesfoto

- **Gate verbreden:** de `is_package=false → reject` in `receiving.py`
  (`:281-287`, `:545-551`, `:1206-1211`) en `skus.py:195-196` mag een gelabelde
  fles toelaten (zie [prompt-wijziging I2](#i2-herschreven-prompts-in-langfuse-plakken)).
- **Scan-modus "Doos" vs "Fles":** koerier kiest bij ontvangst de modus; in
  fles-modus wordt de gate overgeslagen (de SKU is bij het scannen nog niet
  bekend, dus een modus is betrouwbaarder dan auto-detectie). UI landt in
  `receive.tsx`.
- **Gescheiden match-pools:** `matching.py:find_best_matches` krijgt een
  `is_bottle`-filter naast `active`/`sku_ids`. Fles-scans matchen alleen tegen
  flesreferenties, doos-scans alleen tegen doosreferenties. Drempel 0.80 blijft.
- Resultaat: flessen worden net als dozen gescand en geboekt (1 scan = 1 fles =
  1 boeking) via hun eigen referentiebeelden.

## G. Voorraad

- Alleen labelkwestie in `inventory.tsx`; reserveer-/telmechaniek ongewijzigd
  (eenheid-agnostisch).

## H. Rapportage (apart tonen)

- `weekly-summary` en `monthly-boxes` (endpoints in `orders.py`, frontend
  `weekly-summary.tsx`/`monthly-boxes.tsx`) groeperen de tellingen op
  `is_bottle` en tonen **"X dozen · Y flessen"** los van elkaar.

## I. LLM-prompts (alleen Langfuse) + conversie

### I1. Fallbacks verwijderen

- In `embedding.py` de constants verwijderen: `CLASSIFY_PROMPT` (regel 54,
  direct gebruikt in `classify_image`), `CLASSIFY_AND_DESCRIBE_DEFAULT` (68),
  `DESCRIBE_DEFAULT` (94), `EXTRACT_SHIPMENT_SYSTEM_DEFAULT` (564),
  `MATCH_SHIPMENT_ARTICLE_DEFAULT` (765).
- Alle fetches omzetten naar `get_prompt_required(name)`:
  - `describe_package` → `describe-package`
  - `classify_and_describe` → `classify-and-describe`
  - `classify_image` → nieuwe Langfuse-prompt `classify` (nu nog een lokale
    constante)
  - `extract_shipment_document` → `extract-shipment-document`
  - `match_shipment_article` → `match-shipment-article-name`
- Eventueel `get_prompt(name, fallback=...)` uit `langfuse_client.py`
  verwijderen als niets het meer gebruikt.
- **Gevolg:** zonder geconfigureerde Langfuse falen deze calls bewust.
  Vision/extractie vereisen toch al een echte API-key, dus dev draait dit
  normaal niet; de impact zit vooral in **tests** (zie J).

### I2. Herschreven prompts (in Langfuse plakken)

**`classify-and-describe`**
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

**`describe-package`**
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

**`classify`** (nieuw in Langfuse; vervangt de lokale `CLASSIFY_PROMPT`)
```
Analyze this image and respond in EXACTLY this JSON format — no markdown fencing, no extra text:

{"is_package": true, "summary": "brief 5-word description"}

Set "is_package" to true if the image shows an identifiable retail product to match: any box, case, crate, carton or packaging, OR a single sealed, labelled bottle (e.g. a wine, cava or spirits bottle).
Set it to false for loose objects, scenes, furniture, electronics without packaging, food without packaging, a poured glass of wine, or an empty/unlabelled bottle.

Examples of true: wine box, shoe box, cardboard carton, wooden crate, sealed package, shipping parcel, a labelled wine or cava bottle.
Examples of false: a clock, candles on a table, a laptop, a pair of shoes, a glass of wine.
```

**`extract-shipment-document`** — alleen de quantity-regel wijzigen:

> ~~"The backend converts pieces->boxes using a fixed ratio of 6 bottles per box, so you MUST NOT do that math yourself."~~
>
> → "The backend converts the quantity to the product's order unit based on the matched product, so you MUST NOT do any box/bottle math yourself. Report the number and unit exactly as the document states."

(De rest van de extractie-prompt blijft; `extract-shipment-text` en
`match-shipment-article-name` blijven inhoudelijk gelijk maar verliezen hun
lokale fallback.)

### I3. Conversie per producttype (backend)

- `_resolve_inbound_quantity` (`inventory.py:96-118`): na artikelmatching de
  `is_bottle` van de gematchte SKU meenemen.
  - Fles-SKU + `pieces` → aantal flessen = aantal besteleenheden (**niet** delen
    door 6).
  - Fles-SKU + `boxes`/colli → dubbelzinnig → `unknown` zetten zodat de operator
    bevestigt.
  - Doos-SKU → huidig gedrag (de vaste `BOTTLES_PER_BOX = 6` blijft een
    bestaande, aparte beperking — buiten scope).

## J. Seed & tests

- `seed_dev.py`: "Cava 0,0" als `is_bottle=True` (demo).
- **Tests aanpassen** (gevolg van I1): `test_call_vision.py`, `test_langfuse.py`,
  `test_shipment_extract_preview.py` en wat nog op fallbacks leunt →
  `get_prompt_required` mocken/stubben in `conftest.py` zodat tests zonder
  Langfuse blijven werken.
- Nieuwe tests: order met fles-regel + aparte sectie; flesreferentie passeert de
  gate; fles-scan matcht alleen flespool; conversie van pieces voor fles-SKU
  (geen deling door 6); order-aggregaten splitsen dozen/flessen; rapportage
  splitst dozen/flessen; migratie-default.

## K. Randzaken

- **SKU-foutmelding** `skus.tsx:570` "Niet herkend als wijndoos" → misleidend
  voor een fles; tekst aanpassen.
- **Verouderde docstrings/comments** — `models.py:390` en `receiving.py:517`
  ("1 scan = 1 box = 1 booking") opschonen.
- **README/docs** — "How It Works" punt 4 beschrijft expliciet het 1-doos-model;
  bijwerken.
- **Analytics/events** — afhankelijk van de open beslissing hieronder.

---

## Impact-analyse: wat valt buiten scope (loos alarm)

Een codebase-sweep markeerde een aantal dingen als "breekt", maar die gaan uit
van een ander model (fles = 1/6 doos, orders moeten omrekenen). In **ons**
ontwerp is een fles zijn eigen eenheid, dus deze vallen weg:

- **Allocatie** (`allocation.py`) → `compute_allocation(..., sku_id, ...)` werkt
  **per SKU**; binnen één verdeling zijn alle regels dezelfde eenheid. Geen
  wijziging nodig.
- **`line_total` "6× fout" / "prijs per doos"** → klopt al; prijs is per
  eenheid, dus `prijs × aantal` is correct voor een fles-SKU.
- **"6× scannen per doos" / batch-scan / `unit_type` op OrderLine** → niet
  nodig; 1 flesscan = 1 boeking = 1 fles.
- **Voorraad / stock movements / reservering / auto-complete** →
  eenheid-agnostisch, prima.
- **`inbound.tsx` / `_resolve_inbound_quantity`** → afgedekt in I3.

---

## Open beslissingen

- **Analytics-events.** De event-types `box_identified` / `box_booked` zijn
  hardcoded (`receiving.py:296,339,553`); flesboekingen komen daardoor als
  "box_*" in Pinot terecht. Keuze:
  - **(a)** accepteren — alleen labeling in analytics, geen codewijziging; óf
  - **(b)** `is_bottle`/`unit` toevoegen aan de `box_booked`-payload zodat
    flessen apart herkenbaar zijn in Pinot.

---

## Uitvoervolgorde (logische lagen)

1. **A–C** — migratie, model, schema, skus-router, SKU-UI (vlag bestaat en is
   instelbaar).
2. **D–E** — labels + aparte Flessen-sectie + order-aggregaten splitsen
   (flessen bestelbaar).
3. **H** — rapportage-splitsing.
4. **I1–I2** — fallbacks verwijderen + prompts in Langfuse.
5. **F + I3** — scan-modus, gate, match-pools, conversie (scan-keten af).
6. **G + J + K** — voorraadlabels, seed, tests, randzaken.

Stap 1–3 leveren direct zichtbare waarde. Stap 4–5 maken de AI/scan-keten af.

---

## PR-strategie

De wijziging wordt opgesplitst in meerdere samenhangende PR's, elk zelfstandig
groen (tests/lint) en backward-compatible, zodat ze los te reviewen en
incrementeel uit te rollen zijn. Omdat `is_bottle` standaard `false` is,
verandert PR 1 niets aan het live-gedrag.

| PR | Inhoud | Lagen | Hangt af van |
|----|--------|-------|--------------|
| 1 | **Fundament — de vlag.** Migratie, model, schema, skus-router, toggle in `skus.tsx`. Niets gebruikt de vlag nog → 0 gedragsverandering. | A–C | — |
| 2 | **Orderscherm.** `unitLabel`-helper, aparte "Flessen"-sectie, order-aggregaten splitsen. | D, E | PR 1 |
| 3 | **Rapportage.** Weekly-summary + monthly-boxes dozen/flessen apart. | H | PR 1 (parallel met PR 2) |
| 4 | **Vision / scan-keten.** Scan-modus, gate verruimen, match-pools, `receive.tsx`-labels, conversie. Meest risicovolle PR — bewust geïsoleerd. | F, I3 | PR 1 |
| 5 | **Prompts zonder fallback.** Fallback-constants weg → `get_prompt_required`, tests mocken. Coördineren met prompts in het Langfuse-dashboard (deploy-volgorde!). | I1, I2 | PR 1 |
| 6 | **Randzaken.** Foutmelding-tekst, docstrings/comments, README/docs, analytics/event-keuze. | K | PR 1 |

**Werkwijze:** sequentieel mergen naar `main` (PR 2–6 leunen op PR 1). Elke PR op
een eigen branch; reviewen + mergen, daarna de volgende vanaf bijgewerkte `main`
— voorkomt rebase-gedoe met stacked PR's. Tests + migratie horen in de PR die ze
nodig heeft (migratie in PR 1).

---

## Aandachtspunten / risico's

- **Geen fallback = harde Langfuse-afhankelijkheid:** de Langfuse-prompts moeten
  bestaan vóór deploy, anders falen vision/extractie. Benodigd in Langfuse:
  `classify-and-describe`, `describe-package`, `extract-shipment-document`,
  `extract-shipment-text`, `match-shipment-article-name`, plus de **nieuwe**
  `classify`.
- **Langfuse bijwerken gaat niet automatisch:** de code wordt aangepast, maar de
  prompttekst moet handmatig in het dashboard geplakt worden — er is geen
  sync-script.
- **Scan-modus is een bewuste UX-keuze:** zonder modus zou de verbrede gate ook
  in doos-modus losse flessen toelaten (acceptabel door gescheiden match-pools,
  maar minder strak).
