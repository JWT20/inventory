# Event Logging

## Architecture

```
Backend (FastAPI) → Kafka → Apache Pinot (REALTIME table)
```

Business events are published from the backend to a Kafka topic (`warehouse_events`), which Pinot ingests in real time.

## Event Schema

Each event contains:

| Field          | Type   | Description                          |
|----------------|--------|--------------------------------------|
| event_id       | STRING | UUID                                 |
| event_type     | STRING | e.g. `box_identified`, `sku_created` |
| timestamp_ms   | LONG   | Unix epoch in milliseconds           |
| user_id        | INT    | ID of the user who triggered it      |
| username       | STRING | Username                             |
| resource_type  | STRING | e.g. `receiving`, `sku`, `order`     |
| resource_id    | INT    | ID of the related resource           |
| details        | JSON   | Event-specific payload               |

## Where Events Are Published

Events are published via `publish_event()` in `backend/app/events.py`. This is called from the API routers:

- `backend/app/routers/auth.py` — login events
- `backend/app/routers/skus.py` — SKU creation/updates
- `backend/app/routers/receiving.py` — box identification (includes LLM vision output)
- `backend/app/routers/picks.py` — pick events
- `backend/app/routers/orders.py` — order events
- `backend/app/routers/vision.py` — vision processing events

## Querying Events

All queries run from the server via `docker compose exec`:

### List all recent events

```bash
sudo docker compose exec pinot curl -s \
  "http://localhost:9000/sql?sql=SELECT+event_type,username,timestamp_ms+FROM+warehouse_events+ORDER+BY+timestamp_ms+DESC+LIMIT+10" \
  | jq '.resultTable'
```

### See what the LLM did (vision details)

```bash
sudo docker compose exec pinot curl -s \
  "http://localhost:9000/sql?sql=SELECT+event_type,timestamp_ms,json_extract_scalar(details,'$.vision_description','STRING')+AS+vision_desc,json_extract_scalar(details,'$.confidence','FLOAT')+AS+confidence,json_extract_scalar(details,'$.matched_sku_code','STRING')+AS+matched_sku+FROM+warehouse_events+WHERE+event_type='box_identified'+ORDER+BY+timestamp_ms+DESC+LIMIT+10" \
  | jq '.resultTable'
```

### Count events by type

```bash
sudo docker compose exec pinot curl -s \
  "http://localhost:9000/sql?sql=SELECT+event_type,COUNT(*)+AS+cnt+FROM+warehouse_events+GROUP+BY+event_type+ORDER+BY+cnt+DESC" \
  | jq '.resultTable'
```

### Events for a specific user

```bash
sudo docker compose exec pinot curl -s \
  "http://localhost:9000/sql?sql=SELECT+event_type,timestamp_ms,resource_type+FROM+warehouse_events+WHERE+username='admin'+ORDER+BY+timestamp_ms+DESC+LIMIT+20" \
  | jq '.resultTable'
```

## Infrastructure

- **Kafka**: KRaft mode (no Zookeeper), single broker, 7-day retention
- **Pinot**: QuickStart EMPTY mode (controller + broker + server in one container)
- **pinot-init**: One-shot container that creates the schema and REALTIME table on startup
- Config files: `pinot/schema.json` and `pinot/table.json`
