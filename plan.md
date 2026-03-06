# Cross-Docking Transitieplan

## Huidige Problemen

1. **Kapotte modellen**: `orders.py` en `picks.py` importeren `Order`, `OrderLine`, `PickLog` uit `models.py` maar die bestaan niet
2. **Ontbrekende schemas**: `OrderCreate`, `OrderResponse`, `OrderLineResponse`, `PickResult` ontbreken in `schemas.py`
3. **Routers niet geregistreerd**: `main.py` registreert orders en picks routers niet
4. **Ongebruikte frontend**: `scan.tsx` en `orders.tsx` bestaan maar zijn niet gekoppeld in `App.tsx`; `api.ts` mist de bijbehorende API methoden
5. **Geen cross-docking**: na scan is er geen indicatie waar een doos naartoe moet
6. **Picking is niet relevant**: picking (uit magazijn halen) past niet bij cross-docking (directe doorstroom)

---

## Cross-Docking Concept

Bij cross-docking worden inkomende dozen direct gesorteerd naar uitgaande containers per klant:

```
Doos binnenkomst → Scan → Identificeer SKU → Zoek order die deze SKU nodig heeft
→ Toon "Zet op CONTAINER C3 (Klant: Wijnhandel Jansen, nog 5 nodig)"
→ Update ontvangen hoeveelheid → Volgende doos
```

Elke klantorder krijgt een fysieke locatie (rollcontainer, pallet, staging area) toegewezen.
Als dezelfde SKU door meerdere klanten besteld is, alloceert het systeem op basis van prioriteit (oudste order eerst).

---

## Stappen

### Stap 1: Opruimen — Verwijder picking-logica

Picking (magazijn → order verzamelen) is niet relevant voor cross-docking. Verwijder:

**Verwijder bestanden:**
- `backend/app/routers/picks.py` — hele bestand
- `frontend/src/components/scan.tsx` — hele bestand

**Reden:** In cross-docking gaan goederen direct van inkomend naar uitgaand. Er is geen tussenopslag en dus geen picking-stap.

---

### Stap 2: Database modellen toevoegen (`models.py`)

Voeg toe aan `backend/app/models.py`:

```python
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255))
    dock_location: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    received_quantity: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")

    order: Mapped["Order"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()
```

**Velden uitleg:**
- `dock_location`: fysieke locatie (bijv. "C1", "C2", "Pallet A3") — wordt bij ordercreatie of later ingesteld
- `received_quantity`: hoeveel dozen voor deze regel al ontvangen zijn (i.p.v. `picked_quantity`)
- `status`: `pending` → `partial` → `fulfilled` (i.p.v. `picked`)

**PickLog wordt NIET toegevoegd** — niet relevant voor cross-docking.

---

### Stap 3: Schemas toevoegen (`schemas.py`)

Voeg toe aan `backend/app/schemas.py`:

```python
# --- Orders ---
class OrderLineCreate(BaseModel):
    sku_code: str
    quantity: int = Field(..., gt=0)

class OrderCreate(BaseModel):
    order_number: str
    customer_name: str
    dock_location: str | None = None
    lines: list[OrderLineCreate] = Field(..., min_length=1)

class OrderUpdate(BaseModel):
    customer_name: str | None = None
    dock_location: str | None = None

class OrderLineResponse(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    sku_name: str
    quantity: int
    received_quantity: int
    status: str
    model_config = {"from_attributes": True}

class OrderResponse(BaseModel):
    id: int
    order_number: str
    customer_name: str
    dock_location: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    lines: list[OrderLineResponse]
    model_config = {"from_attributes": True}

# --- Cross-Docking Resultaat ---
class DockAssignment(BaseModel):
    order_id: int
    order_number: str
    customer_name: str
    dock_location: str | None
    line_id: int
    quantity_needed: int
    quantity_after: int

class ReceiveResult(BaseModel):
    sku_id: int
    sku_code: str
    sku_name: str
    confidence: float
    assignment: DockAssignment | None = None
```

**`ReceiveResult`** vervangt het huidige `MatchResult` als return type van de receiving endpoint. Het bevat de SKU-match PLUS de dock-toewijzing.

**`DockAssignment`** is het kernstuk: vertelt de medewerker exact waar de doos naartoe moet.

---

### Stap 4: Allocatie service (`services/allocation.py`)

**Nieuw bestand `backend/app/services/allocation.py`:**

```python
def find_allocation(db, sku_id):
    """Vind de oudste openstaande orderregel die deze SKU nodig heeft (FIFO)."""
    line = (
        db.query(OrderLine)
        .join(Order)
        .filter(
            OrderLine.sku_id == sku_id,
            OrderLine.received_quantity < OrderLine.quantity,
            Order.status.in_(["pending", "receiving"]),
        )
        .order_by(Order.created_at.asc())
        .first()
    )
    if not line:
        return None
    return line, line.order

def confirm_receipt(db, line_id):
    """Verhoog received_quantity en update statussen."""
    line = db.get(OrderLine, line_id)
    line.received_quantity = min(line.received_quantity + 1, line.quantity)
    if line.received_quantity >= line.quantity:
        line.status = "fulfilled"
    elif line.received_quantity > 0:
        line.status = "partial"

    order = line.order
    if order.status == "pending":
        order.status = "receiving"
    if all(l.status == "fulfilled" for l in order.lines):
        order.status = "fulfilled"

    db.commit()
    return line, order
```

---

### Stap 5: Orders router herschrijven (`routers/orders.py`)

Herschrijf met correcte model imports en cross-docking aanpassingen:

**Endpoints:**
- `GET /api/orders?status=...` — lijst orders
- `POST /api/orders` — maak order met regels en optionele dock_location
- `GET /api/orders/{id}` — haal order op
- `PATCH /api/orders/{id}` — update customer_name of dock_location
- `PATCH /api/orders/{id}/status` — update status
- `DELETE /api/orders/{id}` — verwijder order

**Wijzigingen:**
- Status values: `pending`, `receiving`, `fulfilled` (i.p.v. `picking`, `completed`)
- `dock_location` veld in create en update
- `received_quantity` i.p.v. `picked_quantity`

---

### Stap 6: Receiving router uitbreiden met cross-docking

Wijzig `backend/app/routers/receiving.py`:

**`POST /api/receiving/identify`** — uitbreiden:
1. Scan box → identify SKU (bestaande logica)
2. **NIEUW:** Zoek allocatie via `find_allocation(db, sku_id)`
3. Return `ReceiveResult` met optionele `DockAssignment`

**`POST /api/receiving/confirm`** — NIEUW endpoint:
1. Ontvang `{ line_id: int }`
2. Roep `confirm_receipt(db, line_id)` aan
3. Publiceer `box_received` event

---

### Stap 7: Main.py — Router registreren

- Importeer en registreer `orders` router
- Geen picks router meer

---

### Stap 8: Event logging uitbreiden

**Nieuwe events:**
- `box_received` — doos ontvangen en toegewezen (sku_code, order_number, customer_name, dock_location, received_quantity, quantity)
- `box_received_no_order` — doos ontvangen maar geen openstaande order
- `order_line_fulfilled` — orderregel volledig ontvangen
- `order_fulfilled` — alle regels van een order zijn ontvangen

---

### Stap 9: Tests

**`backend/tests/conftest.py`** — order/line fixtures toevoegen

**`backend/tests/test_orders.py`** (nieuw):
- CRUD operations
- Orderregel validatie (onbekende SKU)
- Duplicate order_number afwijzing
- dock_location toewijzing

**`backend/tests/test_receiving.py`** (nieuw):
- Identify met en zonder openstaande order
- Confirm endpoint
- FIFO allocatie (oudste order eerst)
- Fulfilled status cascade (line → order)
- Dezelfde SKU bij meerdere klanten

---

### Stap 10: Frontend api.ts uitbreiden

Order API methoden toevoegen:
- `listOrders(status?)`, `createOrder(data)`, `getOrder(id)`
- `updateOrder(id, data)`, `updateOrderStatus(id, status)`, `deleteOrder(id)`
- `confirmReceive(lineId)` — nieuw voor cross-docking

---

### Stap 11: Frontend receive.tsx aanpassen

Nieuwe flow: Scan → **Dock Assignment + Label** → (optioneel) New Product

Na succesvolle scan met order-match:
```
┌─────────────────────────────────┐
│ Château Margaux 2018            │
│ WN-001          98% match       │
├─────────────────────────────────┤
│                                 │
│    🟢 ZET OP CONTAINER C3      │
│    Klant: Wijnhandel Jansen     │
│    Order: ORD-2024-0042         │
│    Nog 5 dozen nodig            │
│                                 │
├─────────────────────────────────┤
│  [   Bevestig & Print Label    ]│
│  [   Volgende doos scannen     ]│
│  Niet correct? Nieuw product    │
└─────────────────────────────────┘
```

Zonder openstaande order:
```
┌─────────────────────────────────┐
│ Château Margaux 2018            │
│ WN-001          98% match       │
├─────────────────────────────────┤
│    ⚠️ GEEN OPENSTAANDE ORDER   │
│    Leg apart op overflow        │
├─────────────────────────────────┤
│  [     Label printen           ]│
│  [   Volgende doos scannen     ]│
└─────────────────────────────────┘
```

---

### Stap 12: Frontend orders.tsx aanpassen

- Voeg `dock_location` veld toe aan NewOrderDialog
- Toon dock_location in orderlijst en detail
- Gebruik `received_quantity` i.p.v. `picked_quantity`
- Status labels: "Open" / "Ontvangen" / "Compleet"

---

### Stap 13: Frontend App.tsx — Orders tab

- Voeg "Orders" tab toe aan navigatie (admin en merchant)
- Import `OrdersPage`

---

### Stap 14: Opruimen

- Verwijder `NEXT_STEPS.md`
- Update `.env.example`: OpenAI → Gemini referenties

---

## Samenvatting Gewijzigde Bestanden

| Bestand | Actie |
|---|---|
| `backend/app/models.py` | Order, OrderLine modellen toevoegen |
| `backend/app/schemas.py` | Order + cross-docking schemas toevoegen |
| `backend/app/routers/orders.py` | Herschrijven met correcte modellen |
| `backend/app/routers/receiving.py` | Cross-docking allocatie + confirm endpoint |
| `backend/app/routers/picks.py` | **VERWIJDEREN** |
| `backend/app/services/allocation.py` | **NIEUW** — allocatielogica |
| `backend/app/main.py` | Orders router registreren |
| `backend/tests/conftest.py` | Order/line fixtures toevoegen |
| `backend/tests/test_orders.py` | **NIEUW** — order CRUD tests |
| `backend/tests/test_receiving.py` | **NIEUW** — cross-docking tests |
| `frontend/src/App.tsx` | Orders tab toevoegen |
| `frontend/src/components/receive.tsx` | Dock assignment UI toevoegen |
| `frontend/src/components/orders.tsx` | dock_location + received_quantity |
| `frontend/src/components/scan.tsx` | **VERWIJDEREN** |
| `frontend/src/lib/api.ts` | Order + confirm API methoden |
| `.env.example` | OpenAI → Gemini |
| `NEXT_STEPS.md` | **VERWIJDEREN** |
