"""Nucleo puro del actor: descarga, normaliza, filtra y rankea precios de
carburantes de las ~11.500 estaciones de servicio de Espana.

No depende del SDK de Apify a proposito: asi se puede probar en local con
pytest y reutilizar como libreria. El wrapper de plataforma esta en main.py.
"""

from __future__ import annotations

import json
import math
import re
import ssl
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

SOURCE_URL = (
    "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes"
    "/PreciosCarburantes/EstacionesTerrestres/"
)

# ---------------------------------------------------------------- descarga ---


def _legacy_tls_session():
    """Sesion requests que SI habla con la sede del Ministerio.

    El endpoint negocia TLS con cifrados que OpenSSL 3 rechaza por nivel de
    seguridad: `requests` normal muere con ConnectionReset antes del HTTP.
    Bajamos SECLEVEL y permitimos renegociacion legacy.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.ssl_ import create_urllib3_context

    class _LegacyAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            ctx = create_urllib3_context(ciphers="DEFAULT@SECLEVEL=1")
            ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
            kwargs["ssl_context"] = ctx
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("https://", _LegacyAdapter())
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; apify-carburantes-es/1.0)",
            "Accept": "application/json",
        }
    )
    return session


def fetch_raw(url: str = SOURCE_URL, retries: int = 4, timeout: int = 180) -> dict:
    """Descarga el JSON oficial. Maneja BOM, TLS legacy y reintentos."""
    session = _legacy_tls_session()
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            # El fichero viene con BOM UTF-8: decodificar con utf-8 pelado falla.
            return json.loads(response.content.decode("utf-8-sig"))
        except Exception as exc:  # reintentamos cualquier fallo de red
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"No se pudo descargar la fuente oficial: {last_error}")


def load_raw_file(path: str) -> dict:
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8-sig"))


# ------------------------------------------------------------ normalizacion ---


def _strip_accents(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def _norm_key(text: str) -> str:
    """'Precio Gasolina 95 E5' -> 'preciogasolina95e5' (sin tildes ni signos)."""
    return re.sub(r"[^a-z0-9]", "", _strip_accents(text).lower())


def slug(text: str) -> str:
    """'Gasolina 95 E5' -> 'gasolina_95_e5'."""
    return re.sub(r"[^a-z0-9]+", "_", _strip_accents(text).lower()).strip("_")


#: Alias comodos -> nombre del campo oficial, para que el cliente no tenga que
#: escribir 'Precio Gases licuados del petroleo'.
FUEL_ALIASES: dict[str, str] = {
    "gasolina_95": "Gasolina 95 E5",
    "gasolina95": "Gasolina 95 E5",
    "sp95": "Gasolina 95 E5",
    "gasolina_98": "Gasolina 98 E5",
    "gasolina98": "Gasolina 98 E5",
    "sp98": "Gasolina 98 E5",
    "diesel": "Gasoleo A",
    "gasoleo": "Gasoleo A",
    "gasoil": "Gasoleo A",
    "diesel_premium": "Gasoleo Premium",
    "agricola": "Gasoleo B",
    "glp": "Gases licuados del petroleo",
    "autogas": "Gases licuados del petroleo",
    "gnc": "Gas Natural Comprimido",
    "gnl": "Gas Natural Licuado",
}


def resolve_fuel(name: str) -> str:
    """Devuelve la clave canonica del carburante (slug) para cualquier alias."""
    key = slug(name)
    if key in FUEL_ALIASES:
        return slug(FUEL_ALIASES[key])
    if key.startswith("precio_"):
        key = key[len("precio_") :]
    return key


def _to_float(value: Any) -> float | None:
    """'1,699' -> 1.699 ; '' -> None (la API deja el campo vacio, no a 0)."""
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


_DAY_INDEX = {"L": 0, "M": 1, "X": 2, "J": 3, "V": 4, "S": 5, "D": 6}
_HOUR_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def _parse_days(part: str) -> set[int] | None:
    part = part.replace(" ", "").replace("Y", "-")
    if not part or part == "24H":
        return set(range(7))
    days: set[int] = set()
    if "-" in part:
        first, _, last = part.partition("-")
        if first in _DAY_INDEX and last in _DAY_INDEX:
            index, end = _DAY_INDEX[first], _DAY_INDEX[last]
            while True:
                days.add(index)
                if index == end:
                    break
                index = (index + 1) % 7
            return days
        return None
    for char in part:
        if char in _DAY_INDEX:
            days.add(_DAY_INDEX[char])
    return days or None


def is_open(schedule: str, moment: datetime) -> bool | None:
    """Interpreta Horario ('L-D: 07:00-22:00', '24H', 'L-V: ...; S: ...').

    Devuelve None si el horario no es parseable: no mentimos con un False.
    """
    if not schedule:
        return None
    text = _strip_accents(schedule).upper().strip()
    weekday = moment.weekday()
    minutes_now = moment.hour * 60 + moment.minute
    any_block = False
    for block in re.split(r"[;,]", text):
        block = block.strip()
        if not block:
            continue
        days_part, _, hours_part = block.partition(":")
        if not hours_part:
            days_part, hours_part = "L-D", block
        elif _HOUR_RANGE.search(days_part + ":" + hours_part[:2]) and days_part.strip().isdigit():
            # caso raro '07:00-22:00' sin dias delante
            days_part, hours_part = "L-D", block
        days = _parse_days(days_part)
        if days is None:
            continue
        if weekday not in days:
            any_block = True
            continue
        if "24H" in hours_part.replace(" ", ""):
            return True
        for match in _HOUR_RANGE.finditer(hours_part):
            any_block = True
            start = int(match.group(1)) * 60 + int(match.group(2))
            end = int(match.group(3)) * 60 + int(match.group(4))
            if end <= start:  # tramo que cruza medianoche
                if minutes_now >= start or minutes_now <= end:
                    return True
            elif start <= minutes_now <= end:
                return True
    return False if any_block else None


def iso_date(price_date: str | None) -> str | None:
    """'15/10/2026 4:21:47' -> '2026-10-15' (ordenable, para la serie temporal)."""
    if not price_date:
        return None
    match = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(price_date))
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


SALE_TYPE = {"P": "publico", "R": "restringido"}
MARGIN = {"D": "derecha", "I": "izquierda", "N": "no_aplica"}


def normalize_station(raw: dict, price_date: str | None = None) -> dict:
    """Pasa un registro crudo del Ministerio a un objeto limpio y tipado."""
    get = {_norm_key(key): value for key, value in raw.items()}.get

    prices: dict[str, float] = {}
    for key, value in raw.items():
        if not _norm_key(key).startswith("precio"):
            continue
        number = _to_float(value)
        if number is None:
            continue  # fuera los campos vacios
        prices[slug(re.sub(r"(?i)^precio\s*", "", key))] = round(number, 4)

    lat = _to_float(get("latitud"))
    lon = _to_float(get("longitudwgs84"))
    if lon is None:
        lon = _to_float(get("longitud"))

    station = {
        "id": str(get("ideess") or "").strip(),
        "brand": (get("rotulo") or "").strip(),
        "address": (get("direccion") or "").strip(),
        "postalCode": (get("cp") or "").strip(),
        "town": (get("localidad") or "").strip(),
        "municipality": (get("municipio") or "").strip(),
        "province": (get("provincia") or "").strip(),
        "provinceId": (get("idprovincia") or "").strip(),
        "municipalityId": (get("idmunicipio") or "").strip(),
        "ccaaId": (get("idccaa") or "").strip(),
        "latitude": lat,
        "longitude": lon,
        "schedule": (get("horario") or "").strip(),
        "roadSide": MARGIN.get((get("margen") or "").strip().upper()),
        "saleType": SALE_TYPE.get((get("tipoventa") or "").strip().upper()),
        "bioethanolPct": _to_float(get("bioetanol")),
        "methylEsterPct": _to_float(get("estermetilico")),
        "prices": prices,
        "currency": "EUR",
        "priceDate": price_date,
    }
    if lat is not None and lon is not None:
        station["mapUrl"] = (
            f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        )
    return {key: value for key, value in station.items() if value not in ("", None)}


# ------------------------------------------------------------------ filtros ---


@dataclass
class Query:
    fuel: str = "gasolina_95_e5"
    provinces: tuple[str, ...] = ()
    municipalities: tuple[str, ...] = ()
    postal_codes: tuple[str, ...] = ()
    brands: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    max_price: float | None = None
    open_now: bool = False
    public_only: bool = True
    rank_by: str = "province"  # none | province | municipality | overall
    top_n: int = 0  # 0 = todas
    include_all_fuels: bool = True
    max_items: int = 0
    now: datetime | None = None

    @classmethod
    def from_input(cls, data: dict) -> "Query":
        def as_tuple(key: str) -> tuple[str, ...]:
            value = data.get(key) or []
            if isinstance(value, str):
                value = [value]
            return tuple(
                _strip_accents(str(item)).strip().lower()
                for item in value
                if str(item).strip()
            )

        return cls(
            fuel=resolve_fuel(data.get("fuel") or "gasolina_95_e5"),
            provinces=as_tuple("provinces"),
            municipalities=as_tuple("municipalities"),
            postal_codes=as_tuple("postalCodes"),
            brands=as_tuple("brands"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            radius_km=data.get("radiusKm"),
            max_price=data.get("maxPrice"),
            open_now=bool(data.get("openNow")),
            public_only=data.get("publicOnly", True),
            rank_by=data.get("rankBy") or "province",
            top_n=int(data.get("topN") or 0),
            include_all_fuels=data.get("includeAllFuels", True),
            max_items=int(data.get("maxItems") or 0),
        )


def _matches_text(value: str, needles: Iterable[str]) -> bool:
    haystack = _strip_accents(value).lower()
    return any(needle in haystack for needle in needles)


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", _strip_accents(text).lower()) if token}


def place_matches(official: str, needle: str) -> bool:
    """Compara un topografico del Ministerio con lo que escribe el cliente.

    El Ministerio invierte el articulo y mete los dos idiomas: 'PALMAS (LAS)',
    'CORUNA (A)', 'BALEARS (ILLES)', 'CASTELLON / CASTELLO', 'ARABA/ALAVA'.
    Nadie escribe eso: aceptamos 'Las Palmas', 'A Coruna', 'Castello'...
    """
    if not official or not needle:
        return False
    flat_official = _strip_accents(official).lower()
    flat_needle = _strip_accents(needle).lower().strip()
    if flat_needle in flat_official:
        return True
    needle_tokens = _tokens(flat_needle)
    return bool(needle_tokens) and needle_tokens <= _tokens(flat_official)


def _matches_place(value: str, needles: Iterable[str]) -> bool:
    return any(place_matches(value, needle) for needle in needles)


def build_results(raw: dict, query: Query) -> dict:
    """Aplica la consulta sobre el JSON crudo y devuelve items + resumen."""
    price_date = raw.get("Fecha")
    stations = raw.get("ListaEESSPrecio") or []
    now = query.now or datetime.now(timezone.utc)
    scraped_at = now.isoformat(timespec="seconds")

    items: list[dict] = []
    for raw_station in stations:
        station = normalize_station(raw_station, price_date)
        price = station.get("prices", {}).get(query.fuel)
        if price is None:
            continue  # no sirve ese producto: no es un 0, es que no lo vende
        if query.public_only and station.get("saleType") == "restringido":
            continue
        if query.provinces and not _matches_place(
            station.get("province", ""), query.provinces
        ):
            continue
        if query.municipalities and not (
            _matches_place(station.get("municipality", ""), query.municipalities)
            or _matches_place(station.get("town", ""), query.municipalities)
        ):
            continue
        if query.postal_codes and station.get("postalCode", "") not in set(
            query.postal_codes
        ):
            continue
        if query.brands and not _matches_text(station.get("brand", ""), query.brands):
            continue

        distance = None
        if (
            query.latitude is not None
            and query.longitude is not None
            and station.get("latitude") is not None
        ):
            distance = haversine_km(
                float(query.latitude),
                float(query.longitude),
                station["latitude"],
                station["longitude"],
            )
            if query.radius_km and distance > float(query.radius_km):
                continue

        open_flag = is_open(station.get("schedule", ""), now.astimezone())
        if query.open_now and open_flag is False:
            continue

        item = dict(station)
        item["fuel"] = query.fuel
        item["price"] = price
        item["scrapedAt"] = scraped_at
        if distance is not None:
            item["distanceKm"] = round(distance, 3)
        if open_flag is not None:
            item["openNow"] = open_flag
        if query.max_price is not None:
            item["belowThreshold"] = price <= float(query.max_price)
        if not query.include_all_fuels:
            item.pop("prices", None)
        items.append(item)

    items.sort(key=lambda item: (item["price"], item.get("distanceKm") or 0))

    # Ranking por zona: lo que la API cruda no da.
    if query.rank_by in ("province", "municipality", "overall"):
        field = {
            "province": "province",
            "municipality": "municipality",
            "overall": None,
        }[query.rank_by]
        counters: dict[str, int] = {}
        for item in items:
            zone = item.get(field, "") if field else "ES"
            counters[zone] = counters.get(zone, 0) + 1
            item["rank"] = counters[zone]
            item["rankScope"] = zone or "desconocido"

    if query.top_n:
        if query.rank_by == "none":
            items = items[: query.top_n]
        else:
            items = [item for item in items if item.get("rank", 1) <= query.top_n]
    if query.max_items:
        items = items[: query.max_items]

    prices = [item["price"] for item in items]
    summary = {
        "fuel": query.fuel,
        "priceDate": price_date,
        "stationsInSource": len(stations),
        "stationsMatched": len(items),
        "cheapest": items[0] if items else None,
        "minPrice": min(prices) if prices else None,
        "maxPrice": max(prices) if prices else None,
        "avgPrice": round(sum(prices) / len(prices), 4) if prices else None,
        "medianPrice": round(sorted(prices)[len(prices) // 2], 4) if prices else None,
        "alertCount": sum(1 for item in items if item.get("belowThreshold")),
        "generatedAt": scraped_at,
    }
    return {"items": items, "summary": summary}


def diff_against_previous(items: list[dict], previous: dict[str, float]) -> dict:
    """Anade priceChange/priceTrend comparando con el snapshot anterior.

    Esto es el valor real del actor: la API oficial solo publica el precio de
    HOY, no guarda historico ni deltas.
    """
    changed = up = down = 0
    for item in items:
        before = previous.get(item["id"])
        if before is None:
            item["priceTrend"] = "new"
            continue
        delta = round(item["price"] - before, 4)
        item["previousPrice"] = before
        item["priceChange"] = delta
        item["priceChangePct"] = round(delta / before * 100, 3) if before else None
        if delta > 0:
            item["priceTrend"] = "up"
            up += 1
            changed += 1
        elif delta < 0:
            item["priceTrend"] = "down"
            down += 1
            changed += 1
        else:
            item["priceTrend"] = "flat"
    return {"changed": changed, "up": up, "down": down}
