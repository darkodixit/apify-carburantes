"""Tests del nucleo contra el JSON real del Ministerio.

Si no existe sample_raw.json se descarga una vez (y se cachea).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.carburantes import (  # noqa: E402
    Query,
    build_results,
    diff_against_previous,
    fetch_raw,
    haversine_km,
    is_open,
    load_raw_file,
    normalize_station,
    resolve_fuel,
)

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_raw.json")


@pytest.fixture(scope="session")
def raw() -> dict:
    if not os.path.exists(SAMPLE):
        import json

        data = fetch_raw()
        with open(SAMPLE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        return data
    return load_raw_file(SAMPLE)


# ------------------------------------------------------------ unitarios ---


def test_resolve_fuel_aliases():
    assert resolve_fuel("diesel") == "gasoleo_a"
    assert resolve_fuel("SP95") == "gasolina_95_e5"
    assert resolve_fuel("GLP") == "gases_licuados_del_petroleo"
    assert resolve_fuel("Precio Gasolina 98 E5") == "gasolina_98_e5"


def test_normalize_station_tipa_precios_y_tira_vacios():
    station = normalize_station(
        {
            "IDEESS": "4375",
            "Rótulo": "Nº 10.935",
            "Dirección": "AVENIDA X, 26",
            "Latitud": "39,211417",
            "Longitud (WGS84)": "-1,539167",
            "Precio Gasolina 95 E5": "1,699",
            "Precio Hidrogeno": "",
            "Tipo Venta": "P",
            "Margen": "D",
            "Horario": "L-D: 07:00-22:00",
            "Provincia": "ALBACETE",
        },
        price_date="15/10/2026 4:21:47",
    )
    assert station["prices"] == {"gasolina_95_e5": 1.699}
    assert "hidrogeno" not in station["prices"]
    assert station["latitude"] == pytest.approx(39.211417)
    assert station["longitude"] == pytest.approx(-1.539167)
    assert station["saleType"] == "publico"
    assert station["roadSide"] == "derecha"
    assert station["mapUrl"].startswith("https://www.google.com/maps/")


def test_is_open_parsea_horarios_reales():
    lunes_10 = datetime(2026, 10, 12, 10, 0)  # lunes
    domingo_23 = datetime(2026, 10, 11, 23, 0)  # domingo
    assert is_open("L-D: 07:00-22:00", lunes_10) is True
    assert is_open("L-D: 07:00-22:00", domingo_23) is False
    assert is_open("L-D: 24H", domingo_23) is True
    assert is_open("L-V: 06:00-22:00; S: 07:00-14:00", domingo_23) is False
    assert is_open("", lunes_10) is None


def test_haversine_las_palmas_madrid():
    km = haversine_km(28.1248, -15.4300, 40.4168, -3.7038)
    assert 1700 < km < 1900


def test_diff_against_previous():
    items = [
        {"id": "1", "price": 1.5},
        {"id": "2", "price": 1.4},
        {"id": "3", "price": 1.3},
    ]
    delta = diff_against_previous(items, {"1": 1.4, "2": 1.4})
    assert items[0]["priceTrend"] == "up"
    assert items[0]["priceChange"] == pytest.approx(0.1)
    assert items[1]["priceTrend"] == "flat"
    assert items[2]["priceTrend"] == "new"
    assert delta == {"changed": 1, "up": 1, "down": 0}


# ----------------------------------------------- integracion datos reales ---


def test_fuente_real_tiene_estructura_esperada(raw):
    assert raw["ResultadoConsulta"] == "OK"
    assert len(raw["ListaEESSPrecio"]) > 10000
    assert raw["Fecha"]


def test_todo_el_pais_gasolina95(raw):
    out = build_results(raw, Query(fuel="gasolina_95_e5", rank_by="overall"))
    assert out["summary"]["stationsMatched"] > 8000
    prices = [item["price"] for item in out["items"]]
    assert prices == sorted(prices)
    assert all(0.5 < price < 3.5 for price in prices)
    assert out["items"][0]["rank"] == 1


def test_filtro_provincia_las_palmas(raw):
    out = build_results(raw, Query(fuel="gasolina_95_e5", provinces=("las palmas",)))
    assert 50 < out["summary"]["stationsMatched"] < 400
    assert all("PALMAS" in item["province"] for item in out["items"])


def test_radio_desde_las_palmas_de_gc(raw):
    out = build_results(
        raw,
        Query(
            fuel="gasoleo_a",
            latitude=28.1248,
            longitude=-15.4300,
            radius_km=10,
            rank_by="overall",
            top_n=5,
        ),
    )
    assert 1 <= len(out["items"]) <= 5
    assert all(item["distanceKm"] <= 10 for item in out["items"])


def test_top_n_por_provincia(raw):
    out = build_results(raw, Query(fuel="gasoleo_a", rank_by="province", top_n=3))
    por_provincia: dict[str, int] = {}
    for item in out["items"]:
        por_provincia[item["province"]] = por_provincia.get(item["province"], 0) + 1
    assert por_provincia
    assert max(por_provincia.values()) <= 3
    assert len(por_provincia) >= 50  # 52 provincias


def test_umbral_marca_alertas(raw):
    out = build_results(raw, Query(fuel="gasolina_95_e5", max_price=1.40))
    flagged = [item for item in out["items"] if item["belowThreshold"]]
    assert out["summary"]["alertCount"] == len(flagged)
    assert all(item["price"] <= 1.40 for item in flagged)


def test_sin_campos_vacios_en_la_salida(raw):
    out = build_results(raw, Query(fuel="gasolina_95_e5", provinces=("las palmas",)))
    for item in out["items"]:
        for key, value in item.items():
            assert value != "", f"{key} vino vacio"
            assert value is not None, f"{key} vino a None"


def test_marca_filtro(raw):
    out = build_results(raw, Query(fuel="gasoleo_a", brands=("ballenoil",)))
    if out["items"]:
        assert all("BALLENOIL" in item["brand"].upper() for item in out["items"])


def test_open_now_no_descarta_horarios_ilegibles(raw):
    momento = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
    out = build_results(
        raw, Query(fuel="gasolina_95_e5", provinces=("las palmas",), open_now=True, now=momento)
    )
    assert out["items"]
    assert all(item.get("openNow", True) is not False for item in out["items"])


def test_place_matches_articulo_invertido():
    from src.carburantes import place_matches

    assert place_matches("PALMAS (LAS)", "las palmas")
    assert place_matches("PALMAS (LAS)", "palmas")
    assert place_matches("CORUÑA (A)", "a coruna")
    assert place_matches("BALEARS (ILLES)", "illes balears")
    assert place_matches("CASTELLÓN / CASTELLÓ", "castello")
    assert place_matches("ARABA/ÁLAVA", "alava")
    assert place_matches("RIOJA (LA)", "la rioja")
    assert not place_matches("MADRID", "barcelona")


def test_iso_date():
    from src.carburantes import iso_date

    assert iso_date("15/10/2026 4:21:47") == "2026-10-15"
    assert iso_date("1/3/2026 0:00:00") == "2026-03-01"
    assert iso_date("") is None
