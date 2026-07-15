"""Seed throwaway sample data for local development.

Idempotent: safe to run repeatedly. Run inside the dev backend container:

    make seed
    # or: docker compose -f docker-compose.dev.yml exec backend python -m scripts.seed_dev

Creates a dev organization, a handful of SKUs, one customer linked to them,
and a login per role (owner / member / courier / customer) so every part of
the UI is reachable. Also seeds a barcode/EAN organization with an active order
so the handscanner pick flow can be tested by *typing* EANs (a handscanner is
just a keyboard). Never run this against production.
"""

from app.auth import hash_password
from app.database import SessionLocal
from app.models import (
    Customer,
    CustomerSKU,
    InventoryBalance,
    Location,
    Order,
    OrderLine,
    Organization,
    SKU,
    SKULocation,
    User,
)

# (code, name, description, is_bottle)
SAMPLE_SKUS = [
    ("CHAT-GRAN-ROO-750", "Château Grand Rouge", "Bordeaux blend, rood, 750ml", False),
    ("DOMA-BLAN-WIT-750", "Domaine Blanc", "Chardonnay, wit, 750ml", False),
    ("PROS-BRUT-SPA-750", "Prosecco Brut", "Mousserend, 750ml", False),
    ("RIOJ-RESE-ROO-750", "Rioja Reserva", "Tempranillo, rood, 750ml", False),
    # Los fles-product: per fles besteld/gescand, niet per doos.
    ("CAVA-ZERO-SPA-750", "Cava 0,0", "Alcoholvrij mousserend, 750ml", True),
]

# (username, password, role, needs_org, is_customer)
SAMPLE_USERS = [
    ("owner", "devowner", "owner", True, False),
    ("member", "devmember", "member", True, False),
    ("koerier", "devkoerier", "courier", False, False),
    ("klant", "devklant", "customer", True, True),
]


def main() -> None:
    db = SessionLocal()
    try:
        org = db.query(Organization).filter_by(slug="dev-wijnhandel").first()
        if org is None:
            org = Organization(name="Dev Wijnhandel", slug="dev-wijnhandel")
            db.add(org)
            db.commit()
            db.refresh(org)

        # Attach the auto-created admin to the dev org so its data is visible.
        admin = db.query(User).filter_by(username="admin").first()
        if admin is not None and admin.organization_id is None:
            admin.organization_id = org.id

        for code, name, desc, is_bottle in SAMPLE_SKUS:
            if db.query(SKU).filter_by(sku_code=code).first() is None:
                db.add(
                    SKU(
                        sku_code=code,
                        name=name,
                        description=desc,
                        category="wine",
                        active=True,
                        organization_id=org.id,
                        is_bottle=is_bottle,
                        product_type="vision",
                    )
                )
        db.commit()

        customer = db.query(Customer).filter_by(name="Restaurant De Proef").first()
        if customer is None:
            customer = Customer(name="Restaurant De Proef", organization_id=org.id)
            db.add(customer)
            db.commit()
            db.refresh(customer)

        for sku in db.query(SKU).filter_by(organization_id=org.id).all():
            exists = (
                db.query(CustomerSKU)
                .filter_by(customer_id=customer.id, sku_id=sku.id)
                .first()
            )
            if exists is None:
                db.add(CustomerSKU(customer_id=customer.id, sku_id=sku.id))
        db.commit()

        # One login per role so the scan / warehouse / customer views are all
        # reachable (the platform admin only sees the admin pages).
        created_users = []
        for username, password, role, needs_org, is_customer in SAMPLE_USERS:
            if db.query(User).filter_by(username=username).first() is not None:
                continue
            db.add(
                User(
                    username=username,
                    email=f"{username}@local",
                    hashed_password=hash_password(password),
                    role=role,
                    organization_id=org.id if needs_org else None,
                    customer_id=customer.id if is_customer else None,
                    is_verified=True,
                )
            )
            created_users.append(f"{username}/{password} ({role})")
        db.commit()

        print(
            f"Seeded dev data: org '{org.name}' (id={org.id}), "
            f"{len(SAMPLE_SKUS)} SKUs, customer '{customer.name}'."
        )
        if created_users:
            print("Created logins: " + ", ".join(created_users))
        else:
            print("Role logins already existed (owner/member/koerier/klant).")

        seed_barcode(db)
    finally:
        db.close()


# (sku_code, name, ean) — dummy 13-digit codes. The scan endpoint matches on the
# exact string (no checkdigit check), so these just need to be unique + memorable.
BARCODE_SKUS = [
    ("SOK-ROOD", "Sokken Rood", "8710000000018"),
    ("SOK-BLAU", "Sokken Blauw", "8710000000025"),
    ("SOK-GROEN", "Sokken Groen", "8710000000032"),
]
BARCODE_ORDER_REF = "SOK-DEMO-1"
BARCODE_CHANNEL_REF = "1262"  # type this at the label step


def seed_barcode(db) -> None:
    """A barcode/EAN org + one active order, so the handscanner pick flow is
    testable by typing EANs (and the channel_reference at the label step)."""
    org = db.query(Organization).filter_by(slug="dev-sokken").first()
    if org is None:
        org = Organization(name="Dev Sokken", slug="dev-sokken")
        db.add(org)
        db.flush()
    # EAN org modules: barcode picking + channel orders (Shopify).
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.commit()
    db.refresh(org)

    for code, name, ean in BARCODE_SKUS:
        sku = db.query(SKU).filter_by(sku_code=code).first()
        if sku is None:
            sku = SKU(
                sku_code=code,
                name=name,
                category="other",
                active=True,
                organization_id=org.id,
                product_type="barcode",
                ean=ean,
            )
            db.add(sku)
            db.flush()
        # Stock so picking does not hit the oversell guard.
        if db.query(InventoryBalance).filter_by(
            sku_id=sku.id, organization_id=org.id
        ).first() is None:
            db.add(InventoryBalance(
                sku_id=sku.id, organization_id=org.id, quantity_on_hand=20
            ))
    db.commit()

    customer = db.query(Customer).filter_by(name="Webshop Klant").first()
    if customer is None:
        customer = Customer(name="Webshop Klant", organization_id=org.id)
        db.add(customer)
        db.flush()

    order = db.query(Order).filter_by(reference=BARCODE_ORDER_REF).first()
    if order is None:
        order = Order(
            organization_id=org.id,
            reference=BARCODE_ORDER_REF,
            status="active",
            channel="shopify",
            external_id="SHOP-DEMO-1",
            channel_reference=BARCODE_CHANNEL_REF,
        )
        db.add(order)
        db.flush()
        for code, _name, _ean in BARCODE_SKUS:
            sku = db.query(SKU).filter_by(sku_code=code).first()
            db.add(OrderLine(
                order_id=order.id,
                sku_id=sku.id,
                customer_id=customer.id,
                klant=customer.name,
                quantity=2,
                booked_count=0,
                delivery_day="wednesday",
            ))
    db.commit()
    db.refresh(order)

    print(
        f"\nBarcode pickflow testdata: org '{org.name}' (id={org.id}), "
        f"order '{order.reference}' (id={order.id}, status={order.status})."
    )
    print("Log in als koerier (koerier/devkoerier) → Scan & Boek → kies deze order.")
    print("Typ deze EANs (2x elk om de order compleet te maken):")
    for code, name, ean in BARCODE_SKUS:
        print(f"  {ean}  ({name})")
    print(f"Labelstap: typ '{BARCODE_CHANNEL_REF}' (klopt) — iets anders = blokkade.")

    seed_locations(db)


# (code, rij, kast, plank, [sku_codes]) — pick locations for the barcode demo.
BARCODE_LOCATIONS = [
    ("AB123", "A", "B", "1", ["SOK-ROOD", "SOK-BLAU"]),
    ("CD201", "C", "D", "2", ["SOK-GROEN"]),
]


def seed_locations(db) -> None:
    """Pick locations + links for the barcode demo, so the Locaties-beheer
    (koerier) has data and the follow-up pick flow can be tested."""
    for code, rij, kast, plank, sku_codes in BARCODE_LOCATIONS:
        loc = db.query(Location).filter_by(code=code).first()
        if loc is None:
            loc = Location(code=code, rij=rij, kast=kast, plank=plank)
            db.add(loc)
            db.flush()
        for sku_code in sku_codes:
            sku = db.query(SKU).filter_by(sku_code=sku_code).first()
            if sku is None:
                continue
            exists = db.query(SKULocation).filter_by(
                location_id=loc.id, sku_id=sku.id
            ).first()
            if exists is None:
                db.add(SKULocation(location_id=loc.id, sku_id=sku.id))
    db.commit()
    print("Piklocaties: AB123 (Sokken Rood + Blauw), CD201 (Sokken Groen).")


if __name__ == "__main__":
    main()
