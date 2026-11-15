"""ST-1: everything personal lives in app_settings, never in git.

Each key holds one JSON document. get() merges what is stored over the
defaults below, so a new field shows up with its default without a migration.
The old ALERT_* environment variables still work as the starting point for
property criteria, so the morning alert behaves the same until someone saves
the settings page.
"""
from __future__ import annotations

import copy
import os

from psycopg.types.json import Jsonb

from .db import connect


def _env_list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def defaults() -> dict:
    commute = os.getenv("ALERT_MAX_COMMUTE")
    return {
        "property_criteria": {
            "mode": "rent",
            "max_price": float(os.getenv("ALERT_MAX_PRICE", "1300")),
            "typologies": _env_list("ALERT_TYPOLOGIES", "T1,T2,T3,T4,T5,T6"),
            "councils": _env_list("ALERT_COUNCILS", "Lisboa,Odivelas,Loures"),
            "max_commute": int(commute) if commute else None,
            "commute_mode": "transit",
        },
        "car_criteria": {
            "max_price": None,
            "min_year": None,
            "max_km": None,
            "body_types": ["carrinha", "suv", "monovolume", "utilitario"],
            "fuels": [],
            "automatic": False,
            "min_seats": 5,
            "isofix": True,
            "km_per_year": 12000,
            "insurance_eur": None,
            "electricity_eur_kwh": 0.20,
        },
        "agent": {
            "budget_eur": 20.0,
            # What one million tokens cost through the gateway. The combo picks
            # the model, so this is a ceiling to keep the cap honest.
            "price_in_eur_mtok": 3.0,
            "price_out_eur_mtok": 15.0,
        },
        "telegram": {"chat_a": None, "chat_b": None},
        "zone_alert": {"drop_pct": 3.0, "weeks": 4},
    }


def get(key: str | None = None, conn=None) -> dict:
    base = defaults()
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    finally:
        if own:
            conn.close()
    stored = {r["key"]: r["value"] for r in rows}
    out = copy.deepcopy(base)
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out[key] if key else out


def put(key: str, value, user_id: int | None = None, conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at, updated_by)
               VALUES (%s, %s, now(), %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
                   updated_at = now(), updated_by = EXCLUDED.updated_by""",
            (key, Jsonb(value), user_id),
        )
        conn.commit()
    finally:
        if own:
            conn.close()
