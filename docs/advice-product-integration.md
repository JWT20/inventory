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

`producer`, `image_url` en `vintage` zijn optioneel. Zonder producent gebruikt
Dockscan de wijnnaam, kleur en het volume voor de zichtbare naam en de nieuwe
SKU-code. `source_product_id`, `name`, `color` en `active` zijn verplicht. Een
lege lijst, dubbele ID's, een contractfout of een HTTP-fout wordt geweigerd
zonder bestaande producten te wijzigen.

Een onbekend ID maakt een vision-wijn-SKU met `is_bottle=true`. Dockscan maakt
daarvoor zelf een deterministische flescode. Als de korte leesbare code al
bestaat, krijgt de nieuwe fles een stabiele suffix op basis van het product-ID.
Ook die alternatieve code wordt op conflicten gecontroleerd; Dockscan voegt
nooit automatisch twee producten samen. Een bekend ID werkt alleen naam,
kenmerken en actieve status bij; de bestaande code en handmatig beheerde
referentiebeelden blijven staan.
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
doosvoorraad. Deze feed toont standaard de locatie `store`: de webshop is nu
alleen afhalen en mag geen magazijnvoorraad verkopen.

De locatie is een parameter, `?inventory_location=warehouse`. Vandaag heeft
niemand hem nodig. Zodra er bezorgorders komen die de koerier uit het magazijn
picked, verkoopt de advies-app uit twee potten en moet hij per aanvraag zeggen
welke hij bedoelt. De parameter staat er daarom vanaf het begin in: dan is dat
later een wijziging bij de aanroeper in plaats van een breuk in een contract
dat dan al live staat.

Migratie 054 verplaatst daarom de bestaande voorraad van álle fles-SKU's naar
`store`; doosvoorraad blijft in het magazijn. Flessen zijn winkelvoorraad: de
toonbank en de webshop verkopen ze, Dockscan picked ze niet. Zonder die
verplaatsing zou de feed direct na de deploy voor elke wijn nul teruggeven en
zou de webshop volledig op uitverkocht staan. De bewegingshistorie blijft op de
oude locatie staan, zodat het audittrail blijft kloppen met wat er destijds
gebeurde.

## Verkopen naar Dockscan

De advies-app meldt een afgeronde toonbankverkoop en Dockscan boekt die direct
van de winkelvoorraad af. Een webshoporder gebruikt de reserveringsstroom
hieronder: betaald is nog niet hetzelfde als fysiek afgehaald.

```http
POST /api/integrations/advice/sales
Authorization: Bearer <ADVICE_SALES_API_KEY>
```

```json
{
  "sale_id": "ord_01J...",
  "channel": "pos",
  "occurred_at": "2026-08-10T14:12:00Z",
  "lines": [
    { "source_product_id": "prd_01H8...", "quantity": 2 }
  ]
}
```

Het antwoord splitst de regels in `applied` (nu geboekt, met de nieuwe
beschikbare voorraad), `duplicate` (al eerder geboekt) en `unmatched`.

Het endpoint faalt bewust **open**. Een kassa aan de balie mag nooit blijven
hangen op een voorraadsysteem:

- **Onbekend product** blokkeert de rest van de verkoop niet; het ID komt terug
  in `unmatched`. Koppel het product en post dezelfde verkoop opnieuw — de al
  geboekte regels blijven staan, de nieuwe komt er alsnog bij.
- **Te weinig voorraad** weigert de verkoop niet. De balans mag negatief worden;
  dat maakt de afwijking zichtbaar in plaats van de kassa te blokkeren. Een
  telling (`count`) zet het recht.
- **Retour** is dezelfde aanroep met een negatieve `quantity`.

Idempotent op `(organisatie, sale_id)` en per regel op `(verkoop, SKU)`, want
een kassa op slechte wifi herhaalt zijn verzoek. Dubbele regels voor hetzelfde
product binnen één verkoop worden opgeteld tot één boeking. Elke geboekte regel
levert een `stock_movement` van het type `sale` met `reference_type`
`advice_sale` op locatie `store`.

De sleutel staat los van de twee leessleutels, zodat schrijfrechten apart
ingetrokken kunnen worden.

## Webshop afhalen

Een order uit wijnadvies1 blijft in wijnadvies1 en verschijnt niet in Scan &
Boek. Vóór de Rabo-betaalpagina wordt de winkelvoorraad atomair gereserveerd:

```http
POST /api/integrations/advice/reservations
Authorization: Bearer <ADVICE_SALES_API_KEY>
```

```json
{
  "external_order_id": "order_01J...",
  "order_reference": "JUR-2026-000123",
  "fulfillment_method": "pickup",
  "inventory_location": "store",
  "lines": [
    { "source_product_id": "prd_01H8...", "quantity": 2 }
  ]
}
```

Onbekende producten of onvoldoende winkelvoorraad weigeren de volledige
reservering met HTTP 409; de betaling mag dan niet starten. Een retry met
dezelfde order en dezelfde regels is een succesvolle no-op zolang de
reservering nog `active` is. Dezelfde order-ID met andere regels wordt
geweigerd, en een al afgehaalde of vrijgegeven reservering ook: die houdt geen
flessen meer apart, dus een tweede betaling zou tegen niet-gereserveerde
voorraad starten.

Bij fysiek afhalen verbruikt één idempotente handeling de reservering én boekt
de flessen af, in dezelfde databasetransactie:

```http
POST /api/integrations/advice/reservations/{order-id}/collect
```

Bij definitief annuleren of terugbetalen vóór afhalen wordt alleen de
reservering vrijgegeven:

```http
POST /api/integrations/advice/reservations/{order-id}/release
```

## Reserveringen in Dockscan zelf

De bovenstaande endpoints zijn machine-naar-machine. Onder **Kanalen** staat
naast Shopify en bol nu ook een kopje `wijnadvies`: welke flessen liggen apart,
voor welk ordernummer, en hoe lang al.

Alleen lezen, met opzet. Een reservering hier opheffen zou Dockscan laten
denken dat de flessen vrij zijn terwijl wijnadvies1 ze nog aan een klant heeft
beloofd. Annuleren hoort dus in de advies-app; die stuurt `release` en dan komt
de voorraad hier vanzelf vrij.

De reservering bewaart `fulfillment_method` en `inventory_location` als
snapshot, en `collect` en `release` rekenen af tegen precies die locatie. De
route is dus geen vaste waarde in de code maar een eigenschap van de order:
nieuwe bezorgorders kunnen als `dockscan` + `warehouse` worden aangemeld zonder
dat bestaande afhaalorders meebewegen.

De twee waarden horen bij elkaar en worden ook zo afgedwongen: `pickup` gaat
over de toonbank en hoort bij `store`, `dockscan` verlaat het magazijn en hoort
bij `warehouse`. Een gekruiste combinatie wordt geweigerd met HTTP 422, en een
bestaande order-ID opnieuw aanmelden met een andere route geeft 409 — anders
zou de tweede aanvraag een andere pot reserveren dan de eerste vasthoudt.

Let op: een `dockscan`-reservering houdt alleen magazijnvoorraad vast. Er komt
geen Dockscan-order van, dus de koerier ziet hem niet in Scan & Boek. Zolang
die stroom niet bestaat, is `pickup` de enige route die in de praktijk gebruikt
wordt.

## Handmatig synchroniseren

De periodieke pull kan tot een uur duren. Een net aangemaakte wijn is eerder
nodig: zodra een pakbon binnenkomt moet de fles koppelbaar zijn. Daarvoor is er
een knop **Synchroniseer nu** in Producten en in het inbound-scherm.

```http
POST /api/skus/advice-sync
```

Alleen voor productbeheerders van de organisatie die aan de feed hangt. De
knop haalt geen referentiebeelden op — dat duurt tientallen seconden per nieuw
product en is niet nodig om te koppelen en te boeken. De periodieke pull haalt
het beeld daarna alsnog binnen, want die importeert alleen voor een product dat
er nog geen heeft. Draait er al een synchronisatie, dan volgt een 409: twee
gelijktijdige snapshots zouden op `uq_skus_org_source_product_id` botsen.
Een interval van `0` schakelt alleen de periodieke pull uit; handmatig
synchroniseren blijft beschikbaar zolang URL, API-key en organisatie zijn
geconfigureerd.

## Geen flesproducten in Dockscan aanmaken

Inbound kan een onbekende doos als concept aanmaken. Voor flessen is dat
geweigerd zolang de organisatie aan de adviesfeed hangt: een concept verzint een
lokale identiteit, waarna de feed voor diezelfde wijn zijn eigen SKU aanmaakt en
de geboekte flessen achterblijven op de kopie die de advies-app niet ziet. De
route voor een onbekende fles is dus: wijn aanmaken in de advies-app,
**Synchroniseer nu**, koppelen, boeken.

Deze beperking geldt alleen voor de geconfigureerde adviesorganisatie. Andere
organisaties behouden hun lokale optie **Losse fles**. Ook via het normale
productformulier kan de adviesorganisatie geen fles zonder Adviesproduct-ID
opslaan; zo kan dezelfde dubbele identiteit niet langs een tweede route alsnog
ontstaan.

## Eenmalige bestaande koppelingen

Bestaande fles-SKU's kunnen via het normale SKU-update-endpoint een
`source_product_id` krijgen. Dockscan weigert een koppeling aan een doos-SKU en
weigert een ID dat binnen dezelfde organisatie al aan een andere fles hangt.

In de interface: open **Producten**, bewerk de bestaande fles, plak het ID uit
wijnadvies1 bij **Adviesproduct-ID** en sla op. De bestaande Dockscan-SKU-code
blijft ongewijzigd. Herhaal dit voor alle bestaande flessen voordat de
productfeed wordt ingeschakeld, zodat de eerste synchronisatie geen dubbele
fles-SKU's kan aanmaken.

## Configuratie

```dotenv
ADVICE_STOCK_API_KEY=<inbound-read-key>
ADVICE_SALES_API_KEY=<inbound-write-key>
ADVICE_STOCK_ORGANIZATION_ID=<organization-id>
ADVICE_PRODUCTS_BASE_URL=https://<advice-app>
ADVICE_PRODUCTS_API_KEY=<outbound-product-key>
ADVICE_PRODUCTS_SYNC_INTERVAL_SECONDS=3600
```

De twee sleutels hebben bewust verschillende richtingen en kunnen onafhankelijk
worden ingetrokken. Lege productfeedconfiguratie of interval `0` schakelt alleen
de periodieke productimport uit; het voorraadendpoint blijft afzonderlijk
configureerbaar.
