"""Seed throwaway sample data for local development.

Idempotent: safe to run repeatedly. Run inside the dev backend container:

    make seed
    # or: docker compose -f docker-compose.dev.yml exec backend python -m scripts.seed_dev

Creates a dev organization, a handful of SKUs, one customer linked to them,
and a login per role (owner / member / courier / customer) so every part of
the UI is reachable. Never run this against production.
"""

from app.auth import hash_password
from app.database import SessionLocal
from app.models import Customer, CustomerSKU, Organization, SKU, User

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
    finally:
        db.close()


if __name__ == "__main__":
    main()
