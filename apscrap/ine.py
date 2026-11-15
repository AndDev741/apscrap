"""Official medians from INE by parish (DT-1).

    sale_ppm  0012234  median €/m² of dwellings sold, last 12 months, quarterly (Dim3 H1 = all)
    rent_ppm  0014696  median €/m² of new lease contracts, last 12 months, quarterly

INE revises past quarters, so every run upserts the whole history. Errors come
back as HTTP 200 with Sucesso.Falso, which is checked. A cell without `valor`
is "dado nulo ou não aplicável" and stored as NULL.

Geography codes are '1A0' (Grande Lisboa, NUTS-2024) plus the DICOFRE. The
parish list comes from the indicator's metadata, filtered to the councils being
followed, whose DICO codes are below.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

import httpx
import psycopg

from .db import connect

log = logging.getLogger(__name__)

BASE = "https://www.ine.pt/ine"
INDICATORS = {
    "sale_ppm": {"varcd": "0012234", "extra": "&Dim3=H1"},
    "rent_ppm": {"varcd": "0014696", "extra": ""},
}
# Grande Lisboa councils (NUTS-2024 prefix 1A0).
DICO = {"Amadora": "1115", "Cascais": "1105", "Lisboa": "1106", "Loures": "1107",
        "Mafra": "1109", "Odivelas": "1116", "Oeiras": "1110", "Sintra": "1111",
        "Vila Franca de Xira": "1114"}
TIMEOUT = 150

_PERIOD = re.compile(r"(\d)\.º Trimestre de (\d{4})")


def _period_end(label: str) -> tuple[str, dt.date] | None:
    m = _PERIOD.search(label)
    if not m:
        return None
    q, y = int(m.group(1)), int(m.group(2))
    end = dt.date(y + (q == 4), 1 if q == 4 else q * 3 + 1, 1) - dt.timedelta(days=1)
    return f"S5A{y}{q}", end


def _parish_name(ine_name: str) -> str:
    return re.sub(r"^União das freguesias de\s+", "", ine_name).strip()


def _geo_codes(client: httpx.Client, varcd: str, councils: list[str]) -> dict[str, tuple[str, str | None]]:
    """geocod -> (council, parish or None for the council row)."""
    meta = client.get(f"{BASE}/json_indicador/pindicaMeta.jsp", params={"varcd": varcd, "lang": "PT"}).json()
    cats = meta[0]["Dimensoes"]["Categoria_Dim"][0]
    want = {"1A0" + DICO[c]: c for c in councils if c in DICO}
    out = {}
    for key, val in cats.items():
        if not key.startswith("Dim_Num2_"):
            continue
        code, name = val[0]["categ_cod"], val[0]["categ_dsg"]
        council = want.get(code[:7])
        if council:
            out[code] = (council, None if len(code) == 7 else _parish_name(name))
    return out


def fetch(councils: list[str]) -> dict:
    rows = []
    with httpx.Client(timeout=TIMEOUT) as client:
        for indicator, spec in INDICATORS.items():
            geos = _geo_codes(client, spec["varcd"], councils)
            url = (f"{BASE}/json_indicador/pindica.jsp?op=2&varcd={spec['varcd']}&Dim1=T"
                   f"&Dim2={','.join(geos)}{spec['extra']}&lang=PT")
            data = client.get(url).json()[0]
            if "Verdadeiro" not in (data.get("Sucesso") or {}):
                raise RuntimeError(f"INE {spec['varcd']}: {data.get('Sucesso')}")
            for label, records in (data.get("Dados") or {}).items():
                period = _period_end(label)
                if not period:
                    continue
                for r in records:
                    geo = geos.get(r.get("geocod"))
                    if not geo:
                        continue
                    value = r.get("valor")
                    rows.append({
                        "indicator": indicator, "period": period[0], "period_end": period[1],
                        "geo_code": r["geocod"], "geo_name": r.get("geodsg"),
                        "council": geo[0], "parish": geo[1],
                        "value": float(value) if value not in (None, "") else None,
                    })
    return {"rows": rows}


def refresh(councils: list[str]) -> dict:
    rows = fetch(councils)["rows"]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO ine_values (indicator, period, period_end, geo_code, geo_name, council, parish, value, fetched_at)
                   VALUES (%(indicator)s, %(period)s, %(period_end)s, %(geo_code)s, %(geo_name)s,
                           %(council)s, %(parish)s, %(value)s, now())
                   ON CONFLICT (indicator, period, geo_code) DO UPDATE
                      SET value = EXCLUDED.value, geo_name = EXCLUDED.geo_name,
                          council = EXCLUDED.council, parish = EXCLUDED.parish, fetched_at = now()""",
                rows,
            )
        conn.commit()
        latest = conn.execute(
            "SELECT indicator, max(period_end) AS last FROM ine_values GROUP BY 1").fetchall()
    return {"rows": len(rows), "latest": {r["indicator"]: str(r["last"]) for r in latest}}


def due(conn: psycopg.Connection, days: int = 7) -> bool:
    """Weekly is plenty: INE publishes these once a quarter."""
    row = conn.execute("SELECT max(fetched_at) AS last FROM ine_values").fetchone()
    return row["last"] is None or row["last"] < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
