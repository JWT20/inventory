# Handover: Scan & Boek — Week-modus

## Samenvatting

De "Scan & Boek" functionaliteit werkt nu op dagniveau (woensdag/donderdag/vrijdag). Dit moet
omgebouwd worden naar weekniveau, inclusief een slim toewijzingsalgoritme dat ervoor zorgt dat
zoveel mogelijk klantorders compleet worden gemaakt wanneer de voorraad beperkt is.

---

## Huidige situatie

### Backend

| Onderdeel | Bestand | Toelichting |
|-----------|---------|-------------|
| Book endpoint | `backend/app/routers/receiving.py:215` | Accepteert `order_id`, matcht SKU via camera, checkt `quantity_on_hand >= 1`, geeft `BookingConfirmation` terug met token |
| Confirm endpoint | `receiving.py:436` | Verwerkt token, maakt `Booking` records, doet `apply_stock_movement(movement_type="pick")`, auto-complete bij 100% |
| Book more endpoint | `receiving.py:551` | Boekt extra dozen van dezelfde SKU bij dezelfde order |
| Stock check | `receiving.py:391-405` | Simpele check: `balance.quantity_on_hand < 1` → 409 error |
| Order model | `backend/app/models.py:~267` | Heeft al `delivery_week: str \| None` veld |
| OrderLine model | `models.py:~290` | Heeft `quantity` (besteld), `booked_count` (gescand), `delivery_day` |
| Orders list API | `backend/app/routers/orders.py:275` | Filtert op rol (courier=active, customer=eigen, org=eigen org) |

### Frontend

| Onderdeel | Bestand | Toelichting |
|-----------|---------|-------------|
| OrderSelectStep | `frontend/src/components/receive.tsx:226` | Groepering op `delivery_day` (wo/do/vr) |
| ScanStep | `receive.tsx:331` | Camera → `api.bookBox(blob, orderId)` |
| ResultStep | `receive.tsx:441` | Toont match, `QuantityPicker` met `max={remaining}` |
| Week helpers | `frontend/src/components/weekly-summary.tsx:51-67` | `getISOWeek()` en `shiftWeek()` — herbruikbaar |
| API client | `frontend/src/lib/api.ts:252` | `listOrders()` zonder week-filter |

---

## Gewenste wijzigingen

### 1. Allocation-algoritme (nieuw)

**Bestand:** `backend/app/services/allocation.py` (nieuw)

**Functie:** `compute_allocation(db, week, sku_id, organization_id) → dict[order_line_id, max_booked]`

**Doel:** Bepaal per SKU per week hoeveel dozen elke klant maximaal mag ontvangen, zodat:

- Zoveel mogelijk orders **compleet** worden afgerond
- Geen enkele klant op **0** eindigt (tenzij wiskundig onmogelijk)

**Dubbele orders:** Dezelfde klant kan meerdere orders in dezelfde week hebben met dezelfde wijn
(verschillende leverdagen = verschillende cross-docking dagen). Elke OrderLine wordt **apart**
behandeld in de allocatie — niet samengevoegd per klant.

**Algoritme (greedy, smallest-first):**

```
Invoer:
  stock = quantity_on_hand (uit InventoryBalance)
  lines = alle actieve OrderLines voor deze SKU + week, gesorteerd op (quantity - booked_count) ASC

Stappen:
  1. remaining_per_line = {line: line.quantity - line.booked_count} voor elke line
  2. Verwijder lines met remaining == 0
  3. available = stock
  4. Als available >= som(remaining): iedereen krijgt alles → return {line: line.quantity}
  5. Als available <= len(lines): verdeel eerlijk — elke line krijgt max 1 doos
     (eerste `available` lines krijgen 1, rest krijgt 0)
  6. Anders (genoeg voor minimaal 1 per line, maar niet genoeg voor alles):
     a. Reserveer 1 doos per line → pool = available - len(lines)
     b. Loop door lines (kleinste remaining eerst):
        - extra = min(remaining - 1, pool)
        - cap[line] = booked_count + 1 + extra
        - pool -= extra
  7. Return caps: {line_id: max_total_booked_count}
```

**Verificatie — voorbeeld 1 (gebruiker):**

```
Stock = 10, A wil 2, B wil 4, C wil 8 (alle booked_count = 0)
Gesorteerd: A(2), B(4), C(8)
Totaal gewenst = 14 > 10, dus niet genoeg
available = 10 > 3 lines, dus stap 6

Reserveer 1 per line → pool = 10 - 3 = 7
Stap A: remaining=2, give = min(2-1, 7) = 1, pool = 6, cap = 0+1+1 = 2 ✓ compleet
Stap B: remaining=4, give = min(4-1, 6) = 3, pool = 3, cap = 0+1+3 = 4 ✓ compleet
Stap C: remaining=8, give = min(8-1, 3) = 3, pool = 0, cap = 0+1+3 = 4 (deels)

Resultaat: A=2, B=4, C=4 ✓
```

**Verificatie — voorbeeld 2 (extreme schaarste):**

```
Stock = 2, 5 orders die elk 2 dozen willen (A=2, B=2, C=2, D=2, E=2)
available = 2 < 5 lines → stap 5: eerlijke verdeling
Eerste 2 lines krijgen elk 1 doos, rest krijgt 0

Resultaat: A=1, B=1, C=0, D=0, E=0
(wiskundig onmogelijk om iedereen minstens 1 te geven)
```

**Verificatie — voorbeeld 3 (genoeg voorraad):**

```
Stock = 20, A=2, B=4, C=8
Totaal gewenst = 14 <= 20 → stap 4: iedereen krijgt alles

Resultaat: A=2, B=4, C=8 ✓
```

### 2. Backend: Week-filter op orders

**Bestand:** `backend/app/routers/orders.py`

**Wijziging:** Voeg optionele `?week=YYYY-WXX` query parameter toe aan `GET /api/orders`.

```python
# Bestaand: alle orders voor de organisatie
# Nieuw: optioneel filteren op delivery_week
@router.get("/orders")
def list_orders(week: str | None = None, ...):
    q = ...  # bestaande filters
    if week:
        q = q.filter(Order.delivery_week == week)
    ...
```

### 3. Backend: Cap-informatie meegeven bij boeking

**Bestanden:** `backend/app/routers/receiving.py`, `backend/app/schemas.py`

**Timing:** Allocatie wordt **alleen berekend bij het scan-resultaat** (`POST /receiving/book`),
niet bij elke boeking. De cap wordt meegestuurd in de response en de frontend gebruikt die totdat
de volgende scan plaatsvindt. De `POST /receiving/book/confirm` en `/book/more` endpoints
herberekenen de allocatie als server-side validatie (409 safety net als de cap ondertussen
veranderd is door een andere scanner).

**Wijziging in `/receiving/book` (scan-resultaat):**
Na SKU-match, roep `compute_allocation()` aan. Voeg toe aan `BookingConfirmation` response:

```python
# Nieuwe velden in BookingConfirmation schema
cap_for_customer: int | None     # max dozen voor deze klant deze week
ordered_by_customer: int | None  # hoeveel deze klant besteld heeft
```

**Wijziging in `/receiving/book/confirm` en `/receiving/book/more`:**
Herbereken allocatie server-side. Als de gevraagde hoeveelheid de cap overschrijdt, return 409:

```json
{
  "detail": "Toewijzingslimiet bereikt",
  "error": "allocation_cap_reached",
  "customer": "Klant X",
  "sku_name": "Wijn Y",
  "cap_for_this_customer": 4,
  "ordered_by_this_customer": 8,
  "other_customers": [
    {"name": "Klant A", "quantity": 2},
    {"name": "Klant B", "quantity": 4}
  ]
}
```

### 4. Frontend: Weekselector in OrderSelectStep

**Bestand:** `frontend/src/components/receive.tsx`

**Wijzigingen:**

- Importeer `getISOWeek` en `shiftWeek` uit `weekly-summary.tsx` (of verplaats naar gedeelde utils)
- Voeg `week` state toe (default: huidige week)
- Vervang dag-groepering door weeknavigatie (← Week → + "Vandaag" knop)
- Pas `api.listOrders()` aan met week-parameter
- Sorteer orders op percentage geboekt (oplopend): 0% bovenaan, 100% onderaan
- Geen extra categorieën of badges — de bestaande `x/10` voortgangsindicator blijft staan

### 5. Frontend: Cap-aware QuantityPicker

**Bestand:** `frontend/src/components/receive.tsx` (ResultStep)

**Wijzigingen:**

- Gebruik `cap_for_customer` uit de API-response als maximum (i.p.v. alleen `remaining`)
- `+` knop is **disabled** wanneer cap bereikt (scanner kan er niet voorbij)
- Toon informatieregel **onder** de picker wanneer cap lager is dan besteld:

  > "Max voor Klant X deze week: 4 van 8 dozen Wijn Y"

- Beide mechanismen werken samen: de `+` knop stopt bij de cap EN de tekst legt uit waarom
- Bij 409 `allocation_cap_reached` (safety net als cap ondertussen veranderd is):
  toon zachte toast/banner melding, **geen** hard blokkeerscherm

### 6. Frontend: API client aanpassen

**Bestand:** `frontend/src/lib/api.ts`

**Wijzigingen:**

```typescript
// Orders: week-filter toevoegen
listOrders: (week?: string) =>
  request(`/orders${week ? `?week=${week}` : ""}`),
```

---

## Wat NIET wijzigt

| Aspect | Reden |
|--------|-------|
| Database schema | `delivery_week` bestaat al op Order, `quantity_on_hand` op InventoryBalance, `booked_count` op OrderLine |
| `apply_stock_movement()` | Blijft ongewijzigd, wordt nog steeds aangeroepen bij confirm |
| Cross-docking flow | 1 order = 1 rolcontainer = 1 klant — dit blijft zo |
| Race condition handling | Niet nodig volgens gebruiker |
| Markering "niet leverbaar" | Orders blijven gewoon open staan als er niet genoeg voorraad is |

---

## Designbeslissingen

### Alleen Variant B (zachte melding)

Wanneer een scanner de cap bereikt voor een klant, krijgt hij een **informatieve melding**:

> "Klant X krijgt deze week 4 van de 8 dozen Wijn Y"

Er is **geen** hard blokkeerscherm ("deze dozen zijn voor een andere klant"). De scanner kan niet
meer boeken dan de cap, maar wordt niet met een alarmerende melding geconfronteerd.

### Greedy smallest-first strategie

De keuze voor "kleinste order eerst compleet maken" is gebaseerd op de bedrijfslogica:
- Een klant die 2 dozen bestelt en er 0 krijgt, is erger dan een klant die 8 bestelt en er 4 krijgt
- Door kleine orders eerst af te ronden, maximaliseer je het aantal tevreden klanten
- De "minimaal 1" regel voorkomt dat een grote besteller helemaal niets krijgt

**Extreme schaarste:** Als er minder dozen dan orders zijn (bijv. 2 dozen voor 5 orders),
wordt eerlijk verdeeld: elke order krijgt maximaal 1 doos (eerste N orders). Niet iedereen
kan minstens 1 krijgen — dat is wiskundig onmogelijk.

### Dezelfde klant, meerdere orders

Dezelfde klant kan meerdere orders in dezelfde week hebben met dezelfde wijn. Dit gebeurt
wanneer de leverdag verschilt (= andere cross-docking dag = andere rolcontainer). Elke
OrderLine wordt **apart** meegenomen in de allocatie, niet samengevoegd per klant.

### Allocatie-timing

De cap wordt berekend bij het **scanresultaat** (1x per scan), niet bij elke boeking.
De frontend gebruikt de meegestuurde cap om de `+` knop te limiteren. De backend
herberekent server-side bij confirm/more als safety net (409 bij overschrijding).
In de praktijk is er meestal 1 scanner actief per organisatie, dus staleness is minimaal.

### Geen voorraad = order blijft open

Als er te weinig voorraad is om een order (deels) te vullen, wordt het order **niet** gemarkeerd
als "niet leverbaar". Het blijft gewoon in de lijst staan. Wanneer er later voorraad binnenkomt
(via pakbon/inbound), kan het alsnog geboekt worden.

---

## Betrokken bestanden (overzicht)

### Nieuw
- `backend/app/services/allocation.py` — compute_allocation functie
- `backend/tests/test_allocation.py` — tests voor het algoritme

### Gewijzigd
- `backend/app/routers/receiving.py` — cap-check integratie in book/confirm/more
- `backend/app/routers/orders.py` — week query parameter
- `backend/app/schemas.py` — cap-velden in BookingConfirmation
- `frontend/src/components/receive.tsx` — weekselector, sorting, cap-aware picker
- `frontend/src/lib/api.ts` — week param op listOrders

### Ongewijzigd
- `backend/app/models.py` — geen migraties nodig
- `backend/app/routers/inventory.py` — `apply_stock_movement` blijft intact
- `frontend/src/components/weekly-summary.tsx` — helpers worden geimporteerd, niet gewijzigd

---

## Teststrategie

### Backend unit tests (`test_allocation.py`)

| Test case | Invoer | Verwacht |
|-----------|--------|----------|
| Gebruikersvoorbeeld | stock=10, A=2, B=4, C=8 | A=2, B=4, C=4 |
| Genoeg voorraad | stock=20, A=2, B=4, C=8 | A=2, B=4, C=8 (allemaal compleet) |
| Extreme schaarste | stock=2, 5 orders van 2 | Eerste 2 orders krijgen elk 1, rest krijgt 0 |
| Schaarste net genoeg | stock=5, 5 orders van 2 | Elk 1 gereserveerd, kleinste orders aangevuld |
| Geen actieve orders | stock=10, geen lines | Lege dict |
| Deels geboekt | stock=5, A besteld 4 / al 2 geboekt | Cap berekend over restant |
| Zelfde klant, 2 orders | stock=6, klant A: order1=3 + order2=2, klant B=4 | 3 lines apart: A2=2, A1=2, B=2 (sorted asc, greedy) |
| Week-filtering | Orders in W15 en W16 | Alleen W15 orders meegenomen |

### Bestaande tests

- Run `pytest backend/tests/` om te verifiëren dat bestaande functionaliteit niet breekt
- Specifiek `test_orders.py` (create, activate, list) en receiving-gerelateerde tests

---

## Volgorde van implementatie

1. **`allocation.py`** — Het algoritme, puur Python, geen dependencies behalve SQLAlchemy
2. **`test_allocation.py`** — Verifieer het algoritme werkt correct
3. **`orders.py`** — Week-filter op GET endpoint (kleine wijziging)
4. **`schemas.py`** — Cap-velden toevoegen aan response schemas
5. **`receiving.py`** — Cap-check integreren in de 3 book-endpoints
6. **`api.ts`** — Week parameter toevoegen aan listOrders
7. **`receive.tsx`** — Frontend: weekselector, sorting, cap-aware picker, Variant B melding
8. **Tests draaien** — Backend test suite + handmatig frontend testen
9. **Commit & push** naar `claude/add-weekly-booking-X67yV`
