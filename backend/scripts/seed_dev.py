"""Seed throwaway sample data for local development.

Idempotent: safe to run repeatedly. Run inside the dev backend container:

    make seed
    # or: docker compose -f docker-compose.dev.yml exec backend python -m scripts.seed_dev

Creates a dev organization, a handful of SKUs, and one customer linked to
them, so the UI has something to show. Never run this against production.
"""

from app.database import SessionLocal
from app.models import Customer, CustomerSKU, Organization, SKU, User

SAMPLE_SKUS = [
    ("CHAT-GRAN-ROO-750", "Château Grand Rouge", "Bordeaux blend, rood, 750ml"),
    ("DOMA-BLAN-WIT-750", "Domaine Blanc", "Chardonnay, wit, 750ml"),
    ("PROS-BRUT-SPA-750", "Prosecco Brut", "Mousserend, 750ml"),
    ("RIOJ-RESE-ROO-750", "Rioja Reserva", "Tempranillo, rood, 750ml"),
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

        for code, name, desc in SAMPLE_SKUS:
            if db.query(SKU).filter_by(sku_code=code).first() is None:
                db.add(
                    SKU(
                        sku_code=code,
                        name=name,
                        description=desc,
                        category="wine",
                        active=True,
                        organization_id=org.id,
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

        print(
            f"Seeded dev data: org '{org.name}' (id={org.id}), "
            f"{len(SAMPLE_SKUS)} SKUs, customer '{customer.name}'."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
