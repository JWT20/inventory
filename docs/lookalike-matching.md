# Lookalike-dozen: waarom de match faalde en wat er nu gebeurt

## Het probleem

Drie varianten van één productlijn komen binnen (Calycanto Bianco / Rosso /
Rosato). De order vraagt er één. De dozen verschillen in één gedrukt woord.

De scanketen werkte zo:

```
foto → Gemini classify-and-describe → tekstbeschrijving
     → gemini-embedding-001 (3072d)
     → pgvector cosine tegen reference_images.embedding
```

Twee dingen liepen daar mis.

**1. De zoekpool bij boeken was de order, niet de catalogus.**
`/receiving/book` zocht alleen tegen `scope_sku_ids` — de SKU's die openstonden
in de order/week. Scande de picker Rosso terwijl alleen Bianco openstond, dan
was Bianco het enige waar Rosso tegen vergeleken werd. Similarity 0.9+, boven de
drempel, geboekt als Bianco. De ongefilterde zoekopdracht liep al wél mee, maar
de kruiscontrole negeerde precies de verkeerde gevallen:

```python
# oud — receiving.py
if s.id in scope_sku_ids and sim >= confidence - settings.ambiguity_margin:
```

Een lookalike buiten scope werd weggegooid. De echte variant was bekend in de
database en werd actief genegeerd.

**2. Cosine op een tekstbeschrijving kan lookalikes niet scheiden.**
Twee beschrijvingen die op één woord na gelijk zijn, geven embeddings die op
ruisniveau van elkaar verschillen. De *volgorde* binnen zo'n cluster is geen
signaal. `ambiguity_margin` markeert dat wel als twijfel, maar wie er bovenaan
staat is willekeurig.

## Wat er nu gebeurt

```
foto → beschrijving → embedding
     → pgvector over de HELE catalogus (top 10)
       + beste matches uit de open orderregels
     → visuele rerank: scanfoto naast de referentiefoto's van de top-kandidaten
     → beslissing
```

**Zoeken gaat altijd over de hele catalogus.** `CATALOG_SEARCH_TOP_N = 10`; de
scope bepaalt pas achteraf wat geboekt mag worden, niet waartegen vergeleken
wordt. Daarnaast worden de beste matches uit de open orderregels apart opgehaald
en terug in de kandidaatset gezet. Zo kan een grote lookalike-cluster niet alle
tien globale plaatsen vullen en daarmee het product dat werkelijk op de order
staat onzichtbaar maken. De visuele selectie bewaart om dezelfde reden ruimte
voor minstens de beste open kandidaat.

**De rerank is de tweede trap, en draait alleen bij close calls.** Letterlijk
elke foto door een LLM halen kan niet (honderden SKU's × foto's per scan).
Twee-traps retrieval komt daar op neer: pgvector haalt breed op, één vision-call
vergelijkt de scan met de referentiefoto's van alles binnen
`rerank_similarity_band` van de beste hit (max `rerank_max_candidates` SKU's,
`rerank_images_per_sku` foto's elk).

`needs_visual_check()` bepaalt of het de moeite is. Twee triggers:

- **Rivalen binnen `ambiguity_margin`.** Dat cluster *is* een lijn lookalikes;
  daar is de volgorde tussen de leden ruis in plaats van signaal.
- **De beste match in de catalogus staat niet open op deze order.** Of de picker
  heeft de verkeerde doos, of een lookalike heeft de juiste verdrongen. Beide
  moeten bekeken worden voordat er iets ter bevestiging wordt aangeboden.

Wint de beste hit met ruime marge én staat hij open, dan is er niets te
beslissen en gaat de scan zonder extra call door. Dat is de normale scan.

Belangrijk onderscheid in `RerankVerdict`: **niet gedraaid omdat het niet nodig
was** (`skip_reason == NOT_NEEDED`) is iets anders dan **wilde draaien maar kon
niet** (`degraded`). Alleen het tweede levert een waarschuwing bij de picker op;
anders zou elke schone scan om bevestiging vragen.

**De beslisboom bij boeken** (`receiving.py`, `book_box`):

| situatie | uitkomst |
|---|---|
| geen close call | voorstel om te bevestigen, geen vision-call |
| rerank zeker, SKU staat open | voorstel om te bevestigen |
| rerank zeker, SKU staat **niet** open, geen open variant dichtbij | 409 — geen boekknop, mét het onderscheidende kenmerk in de melding |
| rerank zeker, SKU staat **niet** open, maar een open variant zit binnen `ambiguity_margin` van die keuze en lag in dezelfde vergelijking | keuzescherm met `manual_review_required`: de open variant met knop, de gekozen variant met foto en zonder knop |
| rerank onzeker | keuzescherm: open SKU's met knop, lookalikes buiten scope met foto en zonder knop |
| rerank herkent geen enkele kandidaat | 404/422 "niet herkend" — nooit terugvallen op de dichtstbijzijnde embedding |
| rerank kon niet draaien (storing, uit) | voorstel, maar altijd met `confirmation_reason` — nooit stil doorboeken |

De laatste twee regels zijn het hart: als de AI het niet zeker weet, is de
uitkomst een vraag aan de picker, geen boeking.

**Waarom "zeker" niet genoeg is om te blokkeren.** Over elke rerank sinds de
pass live ging kwam `certainty` terug als `"high"` — ook bij antwoorden waar het
beslissende detail niet eens in beeld stond, en ook bij `NONE`. Het veld
onderscheidt dus niets. Daarom blokkeert een zeker verdict alleen nog als de
vectorzoektocht niet tegenspreekt dat de doos iets anders is: staat er een open
variant vlak achter de keuze van de rerank, dan is de eerlijke stand "twee
lookalikes, onbeslist" en gaat die naar de picker. Hoe vaak de rerank de
vectorranking overruled staat als score `rerank_override` op elke trace.

## Waarom een weigering geen exception is

Een geweigerde scan is een normale uitkomst, geen storing. `_scan_and_book`
levert daarom een `ScanOutcome` op — voorstel of `ScanRejection` — en pas de
routerfunctie maakt er een HTTP-antwoord van. Zolang de weigering als exception
door de getracete span liep, was "verkeerde doos" in Langfuse niet te
onderscheiden van "Gemini plat": op 18 augustus stond 58% van de scans op ERROR
terwijl het overgrote deel gewone afwijzingen waren.

Elke uitkomst draagt een `reason_code` — `not_ordered`, `sku_full`, `no_stock`,
`cap_reached`, `not_recognized`, `not_a_package`, `needs_reference_image` — die
zowel in de melding aan de picker als in de score `scan_outcome` terechtkomt.
De trace draagt daarnaast `scope_best`, `catalogue_best`, `gap` en het
rerank-verdict, zodat één observatie het hele verhaal vertelt.

Wordt binnen tien minuten in dezelfde picksessie een SKU geboekt die bij een
geweigerde scan als kandidaat op tafel lag, dan krijgt die weigering de score
`recovered_after_rejection` (`services/scan_metrics.py`). Dat is de picker die
zegt dat de doos wél boekbaar was — gratis ground truth om een promptwijziging
of een nieuwe modelversie tegenaan te houden, zonder handmatig labelen.

## Niet-boekbare lookalikes in het keuzescherm

`AlternativeMatch.bookable=false` + `note`. De frontend (`ConfirmStep.tsx`)
rendert die als oranje waarschuwingskaart mét referentiefoto en zónder
bevestigknop. Reden: de doos buiten scope is meestal precies de doos die de
picker vasthoudt — die foto zien is wat de mispick stopt. Verbergen zou de enige
aanwijzing verbergen.

## Instellingen

| setting | default | betekenis |
|---|---|---|
| `RERANK_ENABLED` | `true` | rerank uit = terug naar vector-only (sneller, kan lookalikes niet scheiden) |
| `RERANK_MAX_CANDIDATES` | `4` | max SKU's per rerank-call |
| `RERANK_SIMILARITY_BAND` | `0.10` | hoe ver onder de beste hit nog meegaat |
| `RERANK_IMAGES_PER_SKU` | `2` | referentiefoto's per kandidaat |

Kosten: één extra vision-call **alleen bij close calls**, met 1 scanfoto + tot 8
referentiefoto's. Latency ~1-2s bovenop de keten, en alleen op die scans. Een
scan met een duidelijke winnaar blijft precies zo snel als voorheen.

## Vereist: Langfuse-prompt `rerank-candidates`

Prompts leven alleen in Langfuse, zonder code-fallback (zie `flessen.md`,
sectie I). **Zonder deze prompt draait de rerank niet** en wordt elke scan
gemarkeerd als `visual check unavailable` — veilig, maar de picker moet dan
alles bevestigen. Maak hem aan met deze tekst:

```
You are verifying a warehouse pick. The first image, labelled [SCAN], is a photo
a picker just took of the product in their hands. The images after it are
reference photos of candidate products, each labelled with a letter.

Decide which candidate — if any — is the SAME product as the scan.

These candidates are deliberately near-identical packaging from one product
line. They typically differ in a single detail: a cultivar or cuvée name, a
colour word, a vintage year, a volume, a small badge or a label colour. Find
that detail and use it. Do NOT decide on overall layout, brand, box shape or
dominant colour — those are identical by design and are not evidence.

Read the printed text on the scan and on each reference. Text beats appearance.

Answer with:
- "choice": the letter of the matching candidate, or "NONE" if the scanned
  product is not among them. Answering NONE is correct and expected when the
  picker grabbed a product that is not in the candidate list.
- "certainty": "high" only when you can name the concrete detail that confirms
  the match and rules out the other candidates. Anything else is "low".
- "distinguishing_feature": what the scanned product's OWN label says, in one
  short Dutch phrase. Describe only the scan. Never name the other candidates
  and never phrase it as what the product is not — the picker does not know
  which candidates you were shown, so naming them is confusing.
  Good: "etiket zegt Merlot 2025". Bad: "Merlot, niet Sauvignon Blanc".
  Empty string if nothing decisive is readable.

A wrong "high" answer ships the wrong product to a customer. When the decisive
detail is unreadable, blurred or out of frame, answer "low".
```

De `distinguishing_feature` komt in de 409-melding en in
`confirmation_reason` terecht, dus die tekst leest de picker — vandaar Nederlands.

## Wat dit niet oplost

- **Varianten zonder eigen referentiefoto.** Staat Rosso niet als SKU met een
  verwerkte referentiefoto in de database, dan zit hij niet in de pool en kan
  geen enkele fix hem aanwijzen. Controleer dat eerst bij een mispick.
- **Onleesbare scans.** Is het onderscheidende woord niet in beeld, dan
  antwoordt de rerank terecht `low` en moet de picker kiezen.
- **Korting per regel, btw per regel** en andere zaken buiten de scanketen.
