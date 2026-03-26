# Database ERD — Inventory Platform

```mermaid
erDiagram
    users {
        int id PK
        varchar username UK "unique, indexed"
        varchar email UK
        varchar hashed_password
        varchar role "admin | merchant | courier"
        bool is_active
        bool is_superuser
        bool is_verified
        datetime created_at
    }

    skus {
        int id PK
        varchar sku_code UK "unique, indexed"
        varchar name
        text description
        bool active
        varchar category "wine | beer | coffee | ..."
        datetime created_at
        datetime updated_at
    }

    sku_attributes {
        int id PK
        int sku_id FK
        varchar key "producent, wijnaam, jaargang, ..."
        varchar value
    }

    reference_images {
        int id PK
        int sku_id FK
        varchar image_path
        text vision_description
        vector embedding "3072-dim pgvector"
        varchar processing_status "pending | done | failed"
        varchar description_quality "low | medium | high"
        bool wine_check_overridden
        datetime created_at
    }

    customers {
        int id PK
        varchar name UK "unique, indexed"
        datetime created_at
    }

    customer_skus {
        int id PK
        int customer_id FK
        int sku_id FK
    }

    orders {
        int id PK
        int merchant_id FK
        varchar reference UK "ORD-XXXXXXXX"
        varchar status "draft | pending_images | active | completed | cancelled"
        datetime created_at
        datetime updated_at
    }

    order_lines {
        int id PK
        int order_id FK
        int sku_id FK
        varchar klant "denormalized customer name"
        int customer_id FK "nullable"
        int quantity
        int booked_count
    }

    bookings {
        int id PK
        int order_id FK
        int order_line_id FK
        int sku_id FK
        int scanned_by FK
        varchar scan_image_path
        float confidence
        datetime created_at
    }

    inbound_shipments {
        int id PK
        int merchant_id FK
        varchar supplier_name "nullable"
        varchar reference "nullable"
        varchar status "draft | booked"
        datetime created_at
        datetime booked_at "nullable"
        int booked_by FK "nullable"
    }

    inbound_shipment_lines {
        int id PK
        int shipment_id FK
        int sku_id FK
        int quantity
    }

    inventory_balances {
        int id PK
        int sku_id FK
        int merchant_id FK
        int quantity_on_hand "default 0"
        datetime last_movement_at "nullable"
    }

    stock_movements {
        int id PK
        int sku_id FK
        int merchant_id FK
        varchar movement_type "receive | pick | adjust | count"
        int quantity "positive or negative"
        varchar reference_type "shipment | booking | manual"
        int reference_id "nullable"
        text note "nullable"
        int performed_by FK
        datetime created_at
    }

    %% --- Relationships ---

    skus ||--o{ sku_attributes : "has attributes"
    skus ||--o{ reference_images : "has reference images"
    skus ||--o{ customer_skus : "in catalog"
    skus ||--o{ order_lines : "ordered as"
    skus ||--o{ bookings : "booked as"
    skus ||--o{ inbound_shipment_lines : "received as"
    skus ||--o{ inventory_balances : "tracked in"
    skus ||--o{ stock_movements : "moved as"

    customers ||--o{ customer_skus : "has catalog"
    customers ||--o{ order_lines : "orders from"

    users ||--o{ orders : "merchant creates"
    users ||--o{ bookings : "courier scans"
    users ||--o{ inbound_shipments : "merchant creates"
    users ||--o{ stock_movements : "user performs"

    orders ||--o{ order_lines : "contains"
    orders ||--o{ bookings : "has bookings"

    order_lines ||--o{ bookings : "fulfilled by"

    inbound_shipments ||--o{ inbound_shipment_lines : "contains"
```

## Operational Flow

```mermaid
flowchart LR
    subgraph Inbound
        A[Pallet arriveert] --> B[Pakbon invoeren]
        B --> C[Pakbon boeken]
        C --> D[(voorraad +N)]
    end

    subgraph Cross-dock
        E[Doos scannen] --> F[AI match SKU]
        F --> G[Toewijzen aan klantorder]
        G --> H[Op rolcontainer]
        G --> I[(voorraad -1)]
    end

    subgraph Outbound
        H --> J[Koerier bezorgt]
    end

    D --> E
```

## Stock Movement Types

| Type | Trigger | Qty | Description |
|------|---------|-----|-------------|
| `receive` | Pakbon boeken | +N | Goederen ontvangen van leverancier |
| `pick` | Scan/booking | -1 | Doos gescand en op rolcontainer gezet |
| `adjust` | Handmatige correctie | +/- | Correctie door admin/merchant |
| `count` | Fysieke telling | +/- | Delta na telling (saldo bijstellen) |
