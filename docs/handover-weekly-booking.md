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

**Algoritme (greedy, smallest-first):**

```
Invoer:
  stock = quantity_on_hand (uit InventoryBalance)
  lines = alle actieve OrderLines voor deze SKU + week, gesorteerd op (quantity - booked_count) ASC

Stappen:
  1. remaining_per_line = {line: line.quantity - line.booked_count} voor elke line
  2. Verwijder lines met remaining == 0
  3. available = stock
  4. Reserveer 1 doos per line → beschikbaar = available - len(lines)
     Als available < len(lines): verdeel evenredig (iedereen krijgt minstens 0 of 1)
  5. Loop door lines (kleinste remaining eerst):
     - give = min(remaining, available - reservations_for_rest)
     - cap[line] = booked_count + give
     - available -= give
  6. Return caps
```

**Verificatie met voorbeeld van gebruiker:**

```
Stock = 10, A wil 2, B wil 4, C wil 8 (alle booked_count = 0)
Gesorteerd: A(2), B(4), C(8)
Beschikbaar: 10
Reserveer 1 per resterende: na A → B en C houden 1 elk gereserveerd

Stap A: give = min(2, 10 - 2) = 2 → cap A = 2 ✓ compleet, available = 8
Stap B: give = min(4, 8 - 1) = 4 → cap B = 4 ✓ compleet, available = 4
Stap C: give = min(8, 4 - 0) = 4 → cap C = 4 (deels), available = 0

Resultaat: A=2, B=4, C=4 ✓ (precies wat de gebruiker wil)
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

**Wijziging in `/receiving/book` (scan-resultaat):**
Na SKU-match, roep `compute_allocation()` aan. Voeg toe aan `BookingConfirmation` response:

```python
# Nieuwe velden in BookingConfirmation schema
cap_for_customer: int | None     # max dozen voor deze klant deze week
ordered_by_customer: int | None  # hoeveel deze klant besteld heeft
```

**Wijziging in `/receiving/book/confirm` en `/receiving/book/more`:**
Controleer of de gevraagde hoeveelheid de cap niet overschrijdt. Zo ja, return 409:

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
- Sorteer orders: `booked_count == 0` eerst → bijna klaar → rest → compleet
- Voeg badges toe: voortgangsindicatie per order

### 5. Frontend: Cap-aware QuantityPicker

**Bestand:** `frontend/src/components/receive.tsx` (ResultStep)

**Wijzigingen:**

- Gebruik `cap_for_customer` uit de API-response als maximum (i.p.v. alleen `remaining`)
- Toon informatieregel wanneer cap lager is dan besteld:

  > "Max voor Klant X deze week: 4 van 8 dozen Wijn Y"

- Disable de `+` knop wanneer cap bereikt
- Bij 409 `allocation_cap_reached`: toon zachte melding (Variant B), **geen** hard blokkeerscherm

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
- `frontend/src/components/receive.tsx` — weekselector, sorting, badges, cap-aware picker
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
| Onmogelijk geval | stock=3, 5 orders van 2 | Elk max 1 (behalve evt. rounding) |
| Geen actieve orders | stock=10, geen lines | Lege dict |
| Deels geboekt | stock=5, A besteld 4 / al 2 geboekt | Cap berekend over restant |
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
