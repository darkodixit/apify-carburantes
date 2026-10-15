"""Apify Actor: Spain Fuel Prices (precios de carburantes de Espana).

Lee la API oficial del Ministerio (11.500 estaciones), la deja limpia y le
anade lo que la fuente NO da: historico diario, deltas de precio, ranking por
zona, radio geografico y alertas por umbral.

Monetizacion: pay-per-event (ver README).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from apify import Actor

from .carburantes import (
    Query,
    build_results,
    diff_against_previous,
    fetch_raw,
    iso_date,
)

HISTORY_KV_STORE = "carburantes-history"
HISTORY_DATASET = "carburantes-historico"

EVENT_RUN = "actor-start"
EVENT_STATION = "station-result"
EVENT_HISTORY = "history-snapshot"
EVENT_ALERT = "price-alert"


async def _charge(event_name: str, count: int = 1) -> None:
    """Cobra un evento. No revienta el run si el actor no esta monetizado."""
    if count <= 0:
        return
    try:
        await Actor.charge(event_name=event_name, count=count)
    except Exception as exc:  # actor sin PPE configurado o corriendo en local
        Actor.log.debug(f"charge({event_name}, {count}) no aplicado: {exc}")


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        query = Query.from_input(actor_input)
        store_history = actor_input.get("storeHistory", True)
        webhook_url = (actor_input.get("alertWebhookUrl") or "").strip()

        await _charge(EVENT_RUN)
        await Actor.set_status_message("Descargando la fuente oficial del Ministerio...")

        started = datetime.now(timezone.utc)
        raw = await asyncio.to_thread(fetch_raw)
        total = len(raw.get("ListaEESSPrecio") or [])
        Actor.log.info(
            f"Fuente OK: {total} estaciones, fecha de precios {raw.get('Fecha')}"
        )

        result = build_results(raw, query)
        items, summary = result["items"], result["summary"]

        # --- historico: delta contra el run anterior ------------------------
        history_store = None
        if store_history:
            history_store = await Actor.open_key_value_store(name=HISTORY_KV_STORE)
            snapshot_key = f"last-{query.fuel}"
            previous = await history_store.get_value(snapshot_key) or {}
            delta = diff_against_previous(items, previous)
            summary["priceChanges"] = delta
            await history_store.set_value(
                snapshot_key, {item["id"]: item["price"] for item in items}
            )
            Actor.log.info(
                f"Deltas vs run anterior: {delta['changed']} cambios "
                f"({delta['up']} suben, {delta['down']} bajan)"
            )

        await Actor.push_data(items)
        await _charge(EVENT_STATION, len(items))

        # --- serie temporal acumulada --------------------------------------
        if store_history and items:
            day = iso_date(summary.get("priceDate"))
            history_dataset = await Actor.open_dataset(name=HISTORY_DATASET)
            await history_dataset.push_data(
                [
                    {
                        "date": day,
                        "priceDateRaw": summary.get("priceDate"),
                        "stationId": item["id"],
                        "fuel": item["fuel"],
                        "price": item["price"],
                        "province": item.get("province"),
                        "municipality": item.get("municipality"),
                        "brand": item.get("brand"),
                    }
                    for item in items
                ]
            )
            await _charge(EVENT_HISTORY)

        # --- alertas por umbral --------------------------------------------
        alerts = [item for item in items if item.get("belowThreshold")]
        if alerts:
            await _charge(EVENT_ALERT, len(alerts))
            if webhook_url:
                await _post_webhook(webhook_url, summary, alerts)

        summary["durationSeconds"] = round(
            (datetime.now(timezone.utc) - started).total_seconds(), 2
        )
        await Actor.set_value("OUTPUT", summary)
        cheapest = summary.get("cheapest") or {}
        await Actor.set_status_message(
            f"{summary['stationsMatched']} estaciones | "
            f"min {summary.get('minPrice')} EUR/l en "
            f"{cheapest.get('municipality', '?')} ({cheapest.get('brand', '?')})",
            is_terminal=True,
        )


async def _post_webhook(url: str, summary: dict, alerts: list[dict]) -> None:
    import httpx

    payload = {
        "fuel": summary.get("fuel"),
        "priceDate": summary.get("priceDate"),
        "alertCount": len(alerts),
        "alerts": [
            {
                "id": item["id"],
                "brand": item.get("brand"),
                "municipality": item.get("municipality"),
                "province": item.get("province"),
                "price": item["price"],
                "mapUrl": item.get("mapUrl"),
            }
            for item in alerts[:50]
        ],
        "actorRunId": os.getenv("APIFY_ACTOR_RUN_ID"),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
        Actor.log.info(f"Webhook de alertas -> HTTP {response.status_code}")
    except Exception as exc:
        Actor.log.warning(f"No se pudo avisar al webhook: {exc}")
