# Courier Rate Cards — Implementation Plan

Replaces the single-row `courier_billing_rates` config with a scalable
per-courier / per-customer / per-service tariff layer, plus a deterministic
resolver. Settlement (locking + payout) is explicitly deferred to phase 2.

## Why

The current `courier_billing_rates` table is one global row: every courier,
every customer, every product is billed at the same €0.50/box. That breaks as
soon as real life shows up:

- courier A charges customer X €0.50, customer Y €0.70
- wine is billed per **box**, books per **item/package**
- a different courier has different agreements

Tariffs are a separate concern from operational scan data. So we add a
dedicated tariff layer (rate cards) and keep `bookings` as the operational
source of truth (who scanned what, when). At settlement time we look up the
matching rate card and (phase 2) snapshot the resolved amount.

## Scope

```
Phase 1 (this plan):
  courier_rate_cards table
  rate resolver (most-specific-wins, with reason)
  effective dates
  sku.category -> service_type / unit_type mapping (locked down)
  courier earnings endpoint uses the resolver

Phase 2 (later, when money actually moves):
  courier_settlements + courier_settlement_lines
  amount snapshot at invoice time
  approved / paid workflow
```

---

## Phase 1

### 1. Lock down `sku.category` → service_type / unit_type

`sku.category` is currently free text. Money will depend on it, so a typo
(`books`, `boek`, `Book`) must not silently fall through to the wrong rate.

- Constrain `category` to a fixed set: `VALID_SKU_CATEGORIES = ("wine", "book", "other")`.
  Validate on SKU create/update; reject anything else.
- Add a single mapping constant (not a table — YAGNI):

  ```python
  CATEGORY_SERVICE_MAP = {
      "wine":  ("wine_pick",    "box"),
      "book":  ("book_pick",    "item"),
      "other": ("general_pick", "item"),
  }
  ```

- Resolver derives `(service_type, unit_type)` from the booking's
  `sku.category` via this map. Unknown/NULL category → `general_pick / item`
  (the safe catch-all), and log a warning so it surfaces.

> Migration note: backfill any existing categories outside the allowed set to
> `other` before applying the CHECK, so the constraint does not fail on legacy
> rows.

### 2. `courier_rate_cards` table (migration 028)

```
courier_rate_cards
  id               PK
  courier_id       FK users.id      NULL  -- NULL = applies to all couriers
  customer_id      FK customers.id  NULL  -- NULL = all customers (in scope)
  organization_id  FK organizations NULL  -- NULL = all orgs
  service_type     varchar(30)      NULL  -- NULL = all services
  unit_type        varchar(20)      NOT NULL -- box | item (informational)
  charge_cents     int NOT NULL           -- billed to the customer per unit
  platform_cents   int NOT NULL           -- platform owner's share per unit
  courier_cents    int NOT NULL           -- courier's net share per unit
  effective_from   date NOT NULL
  effective_until  date NULL              -- NULL = open ended
  created_at       timestamp
  updated_at       timestamp
```

Invariant (validated in the API, not the DB): `charge_cents == platform_cents
+ courier_cents`.

**Migration of the existing rate:** convert the current `courier_billing_rates`
row into the global catch-all card:

```
courier_id=NULL, customer_id=NULL, organization_id=NULL, service_type=NULL,
unit_type='box', charge_cents=50, platform_cents=17, courier_cents=33,
effective_from = <project start / today>
```

Keep `courier_billing_rates` for one release as a read fallback, then drop it
in a later migration once rate cards are confirmed live. (Or drop immediately
after seeding the catch-all card — decide at build time.)

### 3. Unique constraint with nullable scope fields (the trap)

Plain Postgres UNIQUE treats every `NULL` as distinct, so a naive unique on
`(courier_id, customer_id, organization_id, service_type, effective_from)`
would happily allow two conflicting "global default" rows. Fix:

- Use **`UNIQUE NULLS NOT DISTINCT`** (Postgres 15+) on
  `(courier_id, customer_id, organization_id, service_type, effective_from)`
  so NULLs collide as intended.
- If we must support Postgres < 15 or SQLite (tests), fall back to a
  normalized sentinel: store `0` instead of `NULL` for "any" in the unique
  index columns, or use partial unique indexes per NULL-combination.

> Decision needed at build time: confirm the Postgres version on the server.
> `deploy/` targets Ubuntu 24.04 → ships Postgres 16, so `NULLS NOT DISTINCT`
> is available in production. SQLite test path will use the sentinel/normalized
> approach or skip the constraint and rely on the resolver + an app-level guard.

This constraint only prevents *exact-scope* duplicates with the same
`effective_from`. **Overlapping date ranges within the same scope** are not
caught by a unique index — guard those in the API on create/update (reject a
card whose `[effective_from, effective_until)` overlaps an existing card of the
same scope).

### 4. The resolver — most specific wins, returns the reason

Input: `courier_id`, `customer_id` (+ its `organization_id`), `service_type`,
and an `on_date` (the booking date).

Candidate cards = all cards where, for each scope field, the card value is
NULL **or** equals the input, AND `effective_from <= on_date < effective_until`
(treating NULL `effective_until` as open).

Precedence (highest first):

| prio | courier | customer | org | service | scope name         |
|------|---------|----------|-----|---------|--------------------|
| 1    | match   | match    | –   | match   | `customer_service` |
| 2    | match   | match    | –   | NULL    | `customer_default` |
| 3    | match   | NULL     | match | match | `org_service`      |
| 4    | match   | NULL     | match | NULL  | `org_default`      |
| 5    | match   | NULL     | NULL | match | `courier_service`  |
| 6    | match   | NULL     | NULL | NULL  | `courier_default`  |
| 7    | NULL    | NULL     | NULL | NULL  | `global_default`   |

Implementation: score each candidate by a weighted specificity
(courier > customer/org > service) and pick the max; ties should be impossible
once the overlap guard (3) is in place, but break ties deterministically by
`effective_from DESC, id DESC` and log a warning.

The resolver returns a small struct, not just numbers:

```python
@dataclass
class ResolvedRate:
    rate_card_id: int
    scope: str          # customer_service | customer_default | ... | global_default
    charge_cents: int
    platform_cents: int
    courier_cents: int
    unit_type: str
```

`scope` + `rate_card_id` are surfaced in the earnings response (at least in a
debug/expanded view) so billing disputes are traceable: "this box was billed
at €0.70 via rate card #14 (customer_service)".

### 5. Earnings endpoint uses the resolver

`GET /api/courier/earnings?month=YYYY-MM` (existing) changes from "count boxes
× one global rate" to "resolve a rate per booking, then aggregate".

- A single month can now mix rates (different customers / services). So the
  aggregation groups by **customer + service_type** and applies the resolved
  rate per group (all bookings in a group on the same date window share a
  card; if a tariff changes mid-month, split by the effective rate).
- Per-group output: customer, service_type, unit_type, boxes/items, charge.
- Keep the courier-facing response free of the platform/courier split (already
  done) — only `charge` per group + total.
- Optionally include `rate_card_id` / `scope` per group behind a debug flag for
  support.

> Performance: for v1, resolving per distinct (customer, service, date-bucket)
> is fine — there are few rate cards. Cache the card set per request; do not
> hit the DB per booking.

### 6. Tests

- Category lock-down: invalid category rejected; legacy backfill.
- Category → service/unit mapping (incl. unknown → general_pick).
- Resolver precedence: one test per precedence row (1–7), plus
  "more specific beats less specific" and "global default is last resort".
- Effective dates: card not yet active / expired is skipped; mid-month tariff
  change splits the month correctly.
- Overlap guard: creating an overlapping same-scope card is rejected.
- `NULLS NOT DISTINCT` (or sentinel) prevents duplicate global rows.
- Earnings: two customers of one courier at different rates aggregate
  correctly; books (item) vs wine (box) billed at their own card.
- Resolver returns correct `scope` + `rate_card_id`.

### 7. Admin UI (minimal, phase 1)

- A "Tarieven" screen (platform admin) to list/create/edit rate cards:
  courier (or "alle"), customer/org (or "alle"), service, amounts, dates.
- Validate `charge == platform + courier` and the overlap guard client-side
  too. Keep it simple — a table + a dialog form.
- The courier still only sees their **Facturatie** screen (no split).

---

## Phase 2 (deferred — do NOT build yet)

Build this only once invoices are actually finalized / couriers are paid out.

- `courier_settlements`: a billing run for a courier + period (status:
  draft → approved → paid).
- `courier_settlement_lines`: one row per billed group, **snapshotting** the
  resolved `rate_card_id`, scope, unit counts and cent amounts at the moment
  of settlement. This is the true history freeze: once a settlement line is
  written, later rate-card edits never change it.
- Approve/paid workflow + an owner/admin payout overview (per courier: units,
  your €0.17 share, amount to pay).

### Why defer

The payout flow (who approves, when, how invoices are issued) is not designed
yet. Snapshotting now would lock in assumptions about that flow. Until then,
history is kept stable by:

1. effective-dated rate cards, **and**
2. making a rate card immutable once a date in its active range has been
   billed (or simply: never hard-edit a past card — supersede it with a new
   card and an `effective_until` on the old one).

This gives "good enough" historical stability for phase 1 without committing to
the settlement schema prematurely.

---

## Open decisions (confirm before building)

1. Drop `courier_billing_rates` immediately after seeding the catch-all card,
   or keep it one release as fallback?
2. Confirm Postgres 16 in prod (→ use `NULLS NOT DISTINCT`); choose the
   SQLite-test strategy (sentinel `0` vs skip-constraint + app guard).
3. Is `organization_id` on a rate card needed in v1, or is `customer_id` +
   `courier_id` enough until per-merchant defaults are actually requested?
   (Leaving the column in but unused is cheap; using it adds a precedence row.)
4. Surface `scope` / `rate_card_id` in the normal earnings response, or only
   behind a debug flag?
