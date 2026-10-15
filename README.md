# ⛽ Spain Fuel Prices — precios de carburantes de España (11.500 gasolineras)

> Official Spanish fuel prices, **cleaned, ranked, geo-filtered and with daily history**.
> Precios oficiales de **todas** las gasolineras de España, limpios, rankeados,
> filtrables por radio y **con histórico diario** (la fuente oficial no lo guarda).

---

## 🇪🇸 Español

### Qué hace

El Ministerio para la Transición Ecológica publica los precios de las ~11.500
estaciones de servicio de España. La API oficial es gratis pero **incómoda de
usar en producción**:

| Problema de la fuente oficial | Qué hace este actor |
|---|---|
| Devuelve **12 MB de un solo golpe**, sin filtros de ningún tipo | Filtras por provincia, municipio, CP, rótulo, carburante, radio en km y precio |
| Rompe la conexión TLS con clientes modernos (OpenSSL 3) | Negociación TLS compatible + 4 reintentos |
| JSON con **BOM** y claves con tildes y espacios (`"Precio Gasolina 95 E5"`) | Claves limpias en inglés, `camelCase`, estables |
| Precios como texto con **coma decimal** (`"1,699"`) | `float` (`1.699`) |
| 30 campos de precio **vacíos** por estación | Solo los productos que la estación sirve de verdad |
| Latitud/longitud como texto con coma | `float` WGS84 + `mapUrl` de Google Maps |
| Nombres oficiales tipo `PALMAS (LAS)`, `CORUÑA (A)`, `ARABA/ÁLAVA` | Escribes «Las Palmas», «A Coruña», «Álava» y funciona |
| **Solo el precio de hoy**: ni histórico ni variaciones | Histórico diario acumulado + `priceChange` / `priceTrend` contra la ejecución anterior |
| No hay ranking ni alertas | Top-N más baratas por provincia/municipio/país + alertas por umbral (con webhook) |
| El horario es una cadena (`"L-V: 06:00-22:00; S: 07:00-14:00"`) | `openNow` booleano, interpretado |

### Para quién

- **Gestores de flotas y transportistas** — ruta de repostaje por la más barata del corredor.
- **Apps y webs comparadoras** — datos listos para pintar, sin ETL propio.
- **Ahorro doméstico / alertas** — «avísame si la 95 baja de 1,40 € en mi municipio».
- **Analistas y periodistas de datos** — serie temporal que la fuente oficial **no** conserva.

### Ejemplos de entrada

Las 5 gasolineras de 95 más baratas a menos de 5 km de tu casa, abiertas ahora,
y avísame si alguna está por debajo de 1,42 €:

```json
{
  "fuel": "gasolina_95_e5",
  "latitude": 28.1248,
  "longitude": -15.43,
  "radiusKm": 5,
  "topN": 5,
  "rankBy": "overall",
  "openNow": true,
  "maxPrice": 1.42,
  "alertWebhookUrl": "https://hooks.tu-dominio.com/gasolina"
}
```

Las 3 más baratas de diésel en cada municipio de la provincia de Las Palmas:

```json
{ "fuel": "diesel", "provinces": ["Las Palmas"], "rankBy": "municipality", "topN": 3 }
```

Todo el país, un carburante, para alimentar tu base de datos:

```json
{ "fuel": "gasoleo_a", "rankBy": "province", "storeHistory": true }
```

Los alias de carburante funcionan: `diesel`, `gasoil`, `sp95`, `sp98`, `glp`,
`autogas`, `gnc`, `gnl`, `agricola`… o el nombre oficial completo.

### Salida (una fila por estación, ya ordenada de más barata a más cara)

```json
{
  "id": "15879",
  "brand": "PLENERGY",
  "address": "CARRETERA DE TEROR KM. 16",
  "postalCode": "35018",
  "town": "TAMARACEITE",
  "municipality": "Palmas de Gran Canaria (Las)",
  "province": "PALMAS (LAS)",
  "latitude": 28.097722,
  "longitude": -15.479889,
  "schedule": "L-D: 24H",
  "openNow": true,
  "saleType": "publico",
  "roadSide": "derecha",
  "fuel": "gasolina_95_e5",
  "price": 1.375,
  "currency": "EUR",
  "prices": { "gasolina_95_e5": 1.375, "gasoleo_a": 1.545 },
  "previousPrice": 1.399,
  "priceChange": -0.024,
  "priceChangePct": -1.716,
  "priceTrend": "down",
  "rank": 1,
  "rankScope": "Palmas de Gran Canaria (Las)",
  "distanceKm": 3.04,
  "belowThreshold": true,
  "mapUrl": "https://www.google.com/maps/search/?api=1&query=28.097722,-15.479889",
  "priceDate": "15/10/2026 4:33:42",
  "scrapedAt": "2026-10-15T02:33:47+00:00"
}
```

Además:

- **`OUTPUT`** (key-value store): resumen del run — mínimo, máximo, media, mediana,
  estación más barata, nº de alertas y nº de precios que han cambiado.
- **Dataset `carburantes-historico`**: una fila por estación/día/carburante
  (`date` en ISO). Ejecútalo a diario con un **Schedule** y en un mes tienes una
  serie temporal que no existe en ninguna fuente pública.

### Programarlo

Apify Console → *Schedules* → cron `0 7 * * *`. La fuente se actualiza varias
veces al día (campo `priceDate`); con una ejecución diaria a primera hora tienes
el precio del día y el delta contra ayer.

### Honestidad sobre los datos

- Los precios son los que **declaran las estaciones** al Ministerio; puede haber
  desfases o descuentos de tarjeta de fidelización no reflejados.
- `openNow` se deduce del texto del horario oficial. Si el horario no es
  interpretable, el campo **no se inventa**: no aparece (y la estación no se
  descarta con `openNow: true`).
- Las estaciones de venta restringida (cooperativas, flotas) se excluyen por
  defecto: `publicOnly: false` para incluirlas.
- Sin datos personales: la fuente solo trae domicilios de instalaciones.

---

## 🇬🇧 English

### What it does

Spain's Ministry for Ecological Transition publishes the price of every one of
the ~11,500 petrol stations in the country. The official API is free but rough:
a single 12 MB payload with no filters, a UTF-8 BOM, accented keys, comma
decimals, 30 empty price fields per station, inverted place names
(`PALMAS (LAS)`), a TLS handshake that modern OpenSSL refuses — and, crucially,
**no history**: only today's price.

This actor gives you that data **production-ready**:

- Filters: fuel, province, municipality, postal code, brand, **radius in km**
  from any lat/lon, price threshold, open-now.
- Clean typed output: floats, WGS84 coordinates, Google Maps link, no empty keys.
- **Cheapest-N ranking** per province / municipality / nationwide.
- **Daily history dataset** + `priceChange`, `priceChangePct`, `priceTrend`
  (`up` / `down` / `flat` / `new`) against the previous run.
- **Price alerts**: `belowThreshold` flag and optional webhook POST.
- Run summary in `OUTPUT`: min / max / avg / median, cheapest station, counts.

### Input

See the input schema — everything is optional except, in practice, `fuel`
(default `gasolina_95_e5`). Fuel aliases accepted: `diesel`, `gasoil`, `sp95`,
`sp98`, `glp`, `autogas`, `gnc`, `gnl`.

### Who buys this

Fleet managers routing refuels, price-comparison apps and sites, consumer
alerting products, and data journalists who need the time series the official
source throws away every few hours.

---

## 💰 Pricing — pay per event

Este actor se monetiza por **evento** (el alquiler mensual de la Store se
retiró el 1-oct-2026). El reparto de Apify es **80 % para el desarrollador**.

| Evento | Cuándo se cobra | Precio |
|---|---|---|
| `actor-start` | 1 por ejecución (cubre descargar los 12 MB y parsear 11.497 estaciones) | **$0,02** |
| `station-result` | 1 por estación entregada, limpia, rankeada y con su delta | **$0,0004** |
| `history-snapshot` | 1 por ejecución que añade el día al histórico acumulado | **$0,01** |
| `price-alert` | 1 por estación por debajo del umbral pedido | **$0,001** |

Los valores exactos están en [`.actor/pay_per_event.json`](.actor/pay_per_event.json)
(mismos nombres de evento que cobra `src/main.py`).

### Por qué sale a 30–150 €/mes

Cuentas con los números medidos hoy (11.497 estaciones en origen, 11.285 sirven
gasóleo A, 255 en la provincia de Las Palmas). Tipo de cambio aproximado
0,92 €/$. **Estimaciones, no ingresos garantizados**: dependen de que haya clientes.

| Perfil de cliente | Consumo mensual | Bruto/mes | Neto dev (80 %) |
|---|---|---|---|
| **A — app local**: 1 provincia, 2 ejecuciones/día | 60 runs × (0,02 + 250×0,0004 + 0,01) | $7,80 | **$6,2 ≈ 5,7 €** |
| **B — alertas regionales**: 3 provincias, 4 ejecuciones/día | 120 runs × ≈0,34 | $40,80 | **$32,6 ≈ 30 €** |
| **C — flota nacional**: todo España, 1 ejecución/día | 30 runs × (0,02 + 11.285×0,0004 + 0,01) | $136,32 | **$109 ≈ 100 €** |
| **D — flota + 2 apps + 1 alertas** | suma de B+C+2×A | $192,72 | **$154 ≈ 142 €** |

Es decir: **un solo cliente de perfil B** ya pone el actor en ~30 €/mes netos, y
**un cliente de flota nacional** en ~100 €/mes. La horquilla 30–150 €/mes es el
rango entre «un cliente serio» y «cuatro clientes mezclados».

El coste de infraestructura lo paga el evento: una ejecución nacional tarda
~25 s y una ejecución provincial ~10 s, muy por debajo de lo que cubre
`actor-start` + `station-result`.

---

## 🛠️ Desarrollo y publicación

```bash
# 1. Tests del núcleo contra los datos reales del Ministerio (16 tests)
python -m pytest tests/test_carburantes.py -v

# 2. Ejecución local completa (crea ./storage con dataset y OUTPUT)
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
#    deja tu entrada en storage/key_value_stores/default/INPUT.json
.venv/Scripts/python -m src

# 3. Publicación en Apify Store  ← ÚNICO paso que requiere la cuenta
npx -y apify-cli login            # abre el navegador, pega el API token
npx -y apify-cli push             # construye la imagen y sube el actor
#    luego, en Apify Console:
#    Monetization -> Pay per event -> pegar los 4 eventos de .actor/pay_per_event.json
#    Publication  -> Publish to Store (categoría: Business / Real estate & Travel)
```

> La cuenta de Apify la crea el titular (plan gratuito, sin tarjeta). El actor y
> la publicación en la Store no cuestan nada; el plan free incluye crédito
> mensual de sobra para las pruebas.

### Estructura

```
.actor/
  actor.json            # metadatos del actor
  input_schema.json     # formulario de entrada (17 campos)
  dataset_schema.json   # vista de tabla «Cheapest first» en la Console
  pay_per_event.json    # pricing exacto a pegar en Monetization
src/
  carburantes.py        # núcleo puro: descarga, normaliza, filtra, rankea, diffea
  main.py               # wrapper de plataforma: storages, cobro, webhook, OUTPUT
  __main__.py
tests/
  test_carburantes.py   # 16 tests, 9 de ellos contra el JSON real
Dockerfile              # apify/actor-python:3.12
```

Fuente: `https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/`
(datos abiertos del Ministerio para la Transición Ecológica y el Reto Demográfico).
