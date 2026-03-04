#!/bin/sh
# Wait for Pinot to be ready, then create schema and table.

PINOT_URL="http://pinot:9000"

echo "Waiting for Pinot controller..."
for i in $(seq 1 30); do
  if curl -sf "$PINOT_URL/health" > /dev/null 2>&1; then
    echo "Pinot is ready."
    break
  fi
  echo "  attempt $i/30 ..."
  sleep 5
done

# Check if schema already exists
if curl -sf "$PINOT_URL/schemas/warehouse_events" > /dev/null 2>&1; then
  echo "Schema 'warehouse_events' already exists, skipping."
else
  echo "Creating schema..."
  curl -sf -X POST "$PINOT_URL/schemas" \
    -H "Content-Type: application/json" \
    -d @/pinot-config/schema.json
  echo ""
fi

# Check if table already exists
if curl -sf "$PINOT_URL/tables/warehouse_events" > /dev/null 2>&1; then
  echo "Table 'warehouse_events' already exists, skipping."
else
  echo "Creating table..."
  curl -sf -X POST "$PINOT_URL/tables" \
    -H "Content-Type: application/json" \
    -d @/pinot-config/table.json
  echo ""
fi

echo "Pinot init complete."
