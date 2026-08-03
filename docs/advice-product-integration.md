# Advies-app: flesproducten en voorraad

Dockscan en de advies-app delen uitsluitend losse-flesproducten. Doos-SKU's
blijven een zelfstandige B2B-stroom in Dockscan en worden niet gekoppeld,
gesynchroniseerd of omgerekend.

## Identiteit

De advies-app kent iedere jaargang-onafhankelijke wijnfamilie een stabiele
`source_product_id` toe. Dockscan bewaart dat ID uitsluitend op de bijbehorende
fles-SKU (`is_bottle=true`). Het ID is uniek per organisatie. `sku_code` blijft
een door Dockscan beheerd magazijnlabel en is geen koppelsleutel.

## Productfeed naar Dockscan

Dockscan haalt bij starten en daarna periodiek het volledige snapshot op:

```http
GET /api/integrations/inventory/products
Authorization: Bearer <ADVICE_PRODUCTS_API_KEY>
```

```json
{
  "products": [
    {
      "source_product_id": "prd_01H8...",
      "producer": "Château Grand Roi",
      "name": "Cuvée Classique",
      "vintage": "2022",
      "color": "red",
      "active": true,
      "image_url": "https://advies.example/api/integrations/inventory/products/prd_01H8.../image"
    }
  ]
}
```

`image_url` en `vintage` zijn optioneel; de andere velden zijn verplicht. Een
lege lijst, dubbele ID's, een contractfout of een HTTP-fout wordt geweigerd
zonder bestaande producten te wijzigen.

Een onbekend ID maakt een vision-wijn-SKU met `is_bottle=true`. Dockscan maakt
daarvoor zelf een deterministische flescode. Een codeconflict wordt gemeld en
niet samengevoegd. Een bekend ID werkt alleen naam, kenmerken en actieve status
bij; de bestaande code en handmatig beheerde referentiebeelden blijven staan.
Een gekoppeld ID dat in een geldig volledig snapshot ontbreekt, wordt inactief,
maar nooit verwijderd.

De advies-app bepaalt of een product commercieel beschikbaar is; Dockscan
bewaart dat als `source_active`. Bij handelaren die de regel
"zonder bruikbaar beeld inactief" aan hebben staan geldt een strengere eis: een
gekoppelde fles is pas actief als de advies-app hem actief noemt **en** er een
bruikbaar referentiebeeld is. Zonder beeld is de fles namelijk niet te scannen
bij het picken en niet verkoopbaar in de webshop. Zodra een beeld alsnog binnen
is en de analyse slaagt, gaat de fles vanzelf actief.

Als een product nog geen referentiebeeld heeft en `image_url` aanwezig is,
downloadt Dockscan één startbeeld, bewaart daarvan zelf een kopie en verwerkt
het via de bestaande beeldanalyse. Daarna blijft Dockscan eigenaar van de
scanreferenties: latere feeds overschrijven of verwijderen geen beelden.

## Voorraad terug naar de advies-app

```http
GET /api/integrations/advice/stock
Authorization: Bearer <ADVICE_STOCK_API_KEY>
```

```json
{
  "items": [
    {
      "source_product_id": "prd_01H8...",
      "sku_code": "CHAT-GRAN-ROO-750-FLES",
      "is_bottle": true,
      "quantity_available": 8
    }
  ]
}
```

Het endpoint blijft een volledig snapshot van alle fles-SKU's van de
geconfigureerde organisatie. Een nog niet gekoppelde fles heeft
`source_product_id: null`; de advies-app negeert die regel. Inactieve flessen
blijven aanwezig met voorraad nul. Beschikbare voorraad is
`max(quantity_on_hand - quantity_reserved, 0)` en wordt nooit aangevuld vanuit
doosvoorraad.

## Eenmalige bestaande koppelingen

Bestaande fles-SKU's kunnen via het normale SKU-update-endpoint een
`source_product_id` krijgen. Dockscan weigert een koppeling aan een doos-SKU en
weigert een ID dat binnen dezelfde organisatie al aan een andere fles hangt.

## Configuratie

```dotenv
ADVICE_STOCK_API_KEY=<inbound-read-key>
ADVICE_STOCK_ORGANIZATION_ID=<organization-id>
ADVICE_PRODUCTS_BASE_URL=https://<advice-app>
ADVICE_PRODUCTS_API_KEY=<outbound-product-key>
ADVICE_PRODUCTS_SYNC_INTERVAL_SECONDS=3600
```

De twee sleutels hebben bewust verschillende richtingen en kunnen onafhankelijk
worden ingetrokken. Lege productfeedconfiguratie of interval `0` schakelt alleen
de periodieke productimport uit; het voorraadendpoint blijft afzonderlijk
configureerbaar.
