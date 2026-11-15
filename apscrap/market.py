"""Market read models: daily aggregates (DT-2), zone statistics for the map and
the agent (IM-1, IM-4), the cartogram layout, and the feed of what changed.

Every figure carries its n. A parish with fewer than MIN_N listings does not
get a median of its own: the zone shows no colour and the detail panel says so.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import statistics
from collections import defaultdict

from .db import connect

log = logging.getLogger(__name__)

MIN_N = 8
DEFAULT_COUNCILS = ["Lisboa", "Odivelas", "Loures"]

# Rural parishes with almost no geocoded adverts, so v_area_centroid has no
# position for them. Approximate centres, used only to place their tile.
KNOWN_CENTRES = {
    ("Loures", "Bucelas"): (38.902, -9.119),
    ("Loures", "Fanhões"): (38.877, -9.165),
    ("Loures", "Lousa"): (38.893, -9.208),
    ("Loures", "Santo Antão e São Julião do Tojal"): (38.855, -9.137),
}


# -- DT-2: nightly aggregates -------------------------------------------------

AGG_SQL = """
INSERT INTO market_agg_daily
    (day, council, parish, transaction_type, property_type, typology, n, median_price, median_ppm)
WITH base AS (
    SELECT s.observed_date AS day, l.council, l.parish, l.transaction_type,
           l.property_type, l.typology, s.price_eur, s.price_per_sqm
    FROM listing_snapshots s
    JOIN listings l ON l.id = s.listing_id
    WHERE s.observed_date BETWEEN %(d0)s AND %(d1)s
      AND l.council IS NOT NULL AND l.transaction_type IS NOT NULL AND l.property_type IS NOT NULL
      AND s.price_eur IS NOT NULL AND s.price_per_sqm IS NOT NULL
      AND l.area_sqm BETWEEN 10 AND 3000
      AND CASE l.transaction_type
              WHEN 'rent' THEN s.price_eur BETWEEN 200 AND 20000 AND s.price_per_sqm BETWEEN 2 AND 100
              WHEN 'sale' THEN s.price_eur BETWEEN 20000 AND 30000000 AND s.price_per_sqm BETWEEN 300 AND 30000
              ELSE false
          END
      AND NOT is_junk_title(s.title)
)
SELECT day, council,
       CASE WHEN GROUPING(parish) = 1 THEN '' ELSE parish END,
       transaction_type, property_type,
       CASE WHEN GROUPING(typology) = 1 THEN '' ELSE typology END,
       count(*),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur)::numeric, 2),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm)::numeric, 2)
FROM base
GROUP BY GROUPING SETS (
    (day, council, parish, transaction_type, property_type, typology),
    (day, council, parish, transaction_type, property_type),
    (day, council, transaction_type, property_type, typology),
    (day, council, transaction_type, property_type)
)
HAVING (GROUPING(parish) = 1 OR parish IS NOT NULL)
   AND (GROUPING(typology) = 1 OR typology IS NOT NULL)
ON CONFLICT (day, council, parish, transaction_type, property_type, typology) DO UPDATE
   SET n = EXCLUDED.n, median_price = EXCLUDED.median_price, median_ppm = EXCLUDED.median_ppm
"""


def refresh_aggregates(days: int = 3) -> dict:
    """Recompute the last `days` days, or every day when the table is empty."""
    with connect() as conn:
        empty = conn.execute("SELECT NOT EXISTS (SELECT 1 FROM market_agg_daily) AS e").fetchone()["e"]
        span = conn.execute("SELECT min(observed_date) AS a, max(observed_date) AS b FROM listing_snapshots").fetchone()
        if span["b"] is None:
            return {"rows": 0}
        d1 = span["b"]
        d0 = span["a"] if empty else d1 - dt.timedelta(days=days - 1)
        n = conn.execute(AGG_SQL, {"d0": d0, "d1": d1}).rowcount
        conn.commit()
    return {"rows": n, "from": str(d0), "to": str(d1)}


def refresh_read_models() -> dict:
    with connect() as conn:
        conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_deals")
        conn.commit()
        n = conn.execute("SELECT count(*) AS n FROM mv_deals").fetchone()["n"]
    return {"mv_deals": n}


# -- names and the cartogram --------------------------------------------------

_ABBR = [(re.compile(r"\bSanta\b"), "Sta."), (re.compile(r"\bSanto\b"), "Sto."),
         (re.compile(r"\bSão\b"), "S."), (re.compile(r"\bNossa Senhora\b"), "N. Sra.")]


def short_name(parish: str) -> str:
    """'Santa Iria de Azoia, São João da Talha e Bobadela' -> 'Sta. Iria de Azoia'."""
    first = re.split(r",| e (?=[A-ZÁÉÍÓÚÂÊÔÃÕ])", parish)[0].strip()
    for rx, rep in _ABBR:
        first = rx.sub(rep, first)
    if first == "Parque das Nações":
        return "P. Nações"
    if first == "Sto. António dos Cavaleiros":
        return "Sto. A. Cavaleiros"
    if len(first) > 18:
        first = first.replace(" de ", " ").replace(" da ", " ").replace(" do ", " ")
    return first


def cartogram(centroids: list[dict], cols: int = 9, row_factor: float = 1.4) -> dict[tuple, tuple[int, int]]:
    """Place each parish on a grid that keeps north up and east right.

    Positions come from the rank of each centroid's longitude and latitude, not
    the raw coordinates: Lisboa's 24 parishes sit in a small corner of the map
    and Loures spreads wide, so raw positions would pile Lisboa onto a few
    tiles. Collisions move to the nearest free cell."""
    if not centroids:
        return {}
    n = len(centroids)
    rows = max(3, round(n / cols * row_factor) + 1)
    by_lon = sorted(centroids, key=lambda c: c["lon"])
    by_lat = sorted(centroids, key=lambda c: -c["lat"])
    lon_rank = {(c["council"], c["parish"]): i / max(n - 1, 1) for i, c in enumerate(by_lon)}
    lat_rank = {(c["council"], c["parish"]): i / max(n - 1, 1) for i, c in enumerate(by_lat)}
    lons, lats = [c["lon"] for c in centroids], [c["lat"] for c in centroids]
    lon_span, lat_span = (max(lons) - min(lons)) or 1, (max(lats) - min(lats)) or 1
    want = {}
    for c in centroids:
        k = (c["council"], c["parish"])
        # Mostly rank, partly real position, so the far north of Loures still
        # reads as far north.
        x = 0.6 * lon_rank[k] + 0.4 * (c["lon"] - min(lons)) / lon_span
        y = 0.6 * lat_rank[k] + 0.4 * (max(lats) - c["lat"]) / lat_span
        want[k] = (x * (cols - 1), y * (rows - 1))

    # Global greedy matching: every (parish, cell) pair by distance, shortest
    # first. Fairer than placing parishes one by one, where the last ones get
    # pushed to the edge.
    cells = [(cx, cy) for cx in range(cols) for cy in range(rows + 2)]
    pairs = sorted(((cx - x) ** 2 + (cy - y) ** 2, k, (cx, cy))
                   for k, (x, y) in want.items() for cx, cy in cells
                   if abs(cx - x) <= 3 and abs(cy - y) <= 3)
    taken: set[tuple[int, int]] = set()
    out = {}
    for _d, k, cell in pairs:
        if k in out or cell in taken:
            continue
        taken.add(cell)
        out[k] = (cell[0] + 1, cell[1] + 1)   # CSS grid lines start at 1
    for k in want:
        if k not in out:   # nothing free nearby: first free cell
            cell = next(c for c in cells if c not in taken)
            taken.add(cell)
            out[k] = (cell[0] + 1, cell[1] + 1)
    return out


# -- zone statistics ------------------------------------------------------------

def _median(values):
    return statistics.median(values) if values else None


def _change(series: list[tuple[dt.date, float, int]], days: int) -> float | None:
    """Percent change of the median €/m² between the last week and the week
    `days` days before it, both with n >= MIN_N on every day used."""
    if not series:
        return None
    last = series[-1][0]
    end = [v for d, v, n in series if last - dt.timedelta(days=6) <= d <= last and n >= MIN_N]
    lo, hi = last - dt.timedelta(days=days + 6), last - dt.timedelta(days=days)
    start = [v for d, v, n in series if lo <= d <= hi and n >= MIN_N]
    if not end or not start:
        return None
    a, b = statistics.median(start), statistics.median(end)
    return round((b - a) / a * 100, 1) if a else None


def _ine_change(points: list[tuple[dt.date, float]]) -> float | None:
    if len(points) < 2:
        return None
    last_d, last_v = points[-1]
    year_ago = [v for d, v in points if abs((last_d - d).days - 365) <= 45]
    if not year_ago or not year_ago[-1]:
        return None
    return round((last_v - year_ago[-1]) / year_ago[-1] * 100, 1)


def zones(mode: str = "rent", councils: list[str] | None = None, typology: str | None = None,
          property_type: str = "flat") -> dict:
    """Everything the map and the zone panel show, for every parish of the
    councils being followed."""
    councils = councils or DEFAULT_COUNCILS
    other = "sale" if mode == "rent" else "rent"
    typ_filter = "" if not typology else (
        "AND typology >= 'T4'" if typology == "T4+" else "AND typology = %(typ)s")
    with connect() as conn:
        cur = conn.execute(
            f"""SELECT council, parish, transaction_type, price_per_sqm, days_on_market,
                       chain_days_on_market, first_seen_at
                  FROM mv_deals
                 WHERE council = ANY(%(c)s) AND parish IS NOT NULL
                   AND property_type = %(pt)s {typ_filter}""",
            {"c": councils, "pt": property_type, "typ": typology},
        ).fetchall()
        cents = conn.execute(
            """SELECT council, parish, lat, lon FROM v_area_centroid
                WHERE council = ANY(%(c)s) AND council_level = 0""", {"c": councils}).fetchall()
        agg = conn.execute(
            """SELECT day, council, parish, transaction_type, n, median_ppm
                 FROM market_agg_daily
                WHERE council = ANY(%(c)s) AND property_type = %(pt)s AND typology = %(t)s
                  AND day > current_date - 400
                ORDER BY day""",
            {"c": councils, "pt": property_type, "t": typology or ""}).fetchall()
        ine = conn.execute(
            """SELECT indicator, council, parish, period_end, value FROM ine_values
                WHERE council = ANY(%(c)s) AND value IS NOT NULL ORDER BY period_end""",
            {"c": councils}).fetchall()

    by_zone = defaultdict(lambda: {"rent": [], "sale": [], "dom": [], "new7": 0})
    week_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    for r in cur:
        z = by_zone[(r["council"], r["parish"])]
        z[r["transaction_type"]].append(float(r["price_per_sqm"]))
        if r["transaction_type"] == mode:
            z["dom"].append(r["chain_days_on_market"] or r["days_on_market"] or 0)
            if r["first_seen_at"] and r["first_seen_at"] > week_ago:
                z["new7"] += 1
    series = defaultdict(list)
    for r in agg:
        series[(r["council"], r["parish"], r["transaction_type"])].append(
            (r["day"], float(r["median_ppm"]), r["n"]))
    ine_series = defaultdict(list)
    for r in ine:
        ine_series[(r["council"], r["parish"] or "", r["indicator"])].append((r["period_end"], float(r["value"])))

    ine_key = "rent_ppm" if mode == "rent" else "sale_ppm"
    names = ({(c["council"], c["parish"]) for c in cents} | set(by_zone)
             | {(r["council"], r["parish"]) for r in ine if r["parish"]})
    centroid_list = [dict(c) for c in cents]
    for k in names - {(c["council"], c["parish"]) for c in cents}:
        if k in KNOWN_CENTRES:
            centroid_list.append({"council": k[0], "parish": k[1],
                                  "lat": KNOWN_CENTRES[k][0], "lon": KNOWN_CENTRES[k][1]})
            continue
        # otherwise park it by its council's mean
        same = [c for c in cents if c["council"] == k[0]]
        if same:
            centroid_list.append({"council": k[0], "parish": k[1],
                                  "lat": statistics.mean(c["lat"] for c in same),
                                  "lon": statistics.mean(c["lon"] for c in same)})
    layout = cartogram(centroid_list)

    out = []
    for (council, parish) in sorted(names):
        z = by_zone[(council, parish)]
        vals, others = z[mode], z[other]
        n, n_other = len(vals), len(others)
        med = round(_median(vals), 2) if n >= MIN_N else None
        med_other = round(_median(others), 2) if n_other >= MIN_N else None
        s = series[(council, parish, mode)]
        own = {d: _change(s, d) for d in (90, 180, 365)}
        ine_pts = ine_series[(council, parish, ine_key)]
        ine12 = _ine_change(ine_pts)
        rent_m = med if mode == "rent" else med_other
        sale_m = med if mode == "sale" else med_other
        gross_yield = round(rent_m * 12 / sale_m * 100, 2) if rent_m and sale_m else None
        weekly = _weekly(s)
        spark = {"source": "apscrap", "points": weekly} if len(weekly) >= 4 else (
            {"source": "ine", "points": [[str(d), v] for d, v in ine_pts[-8:]]} if len(ine_pts) >= 2
            else {"source": "apscrap", "points": weekly})
        pos = layout.get((council, parish))
        out.append({
            "council": council, "parish": parish, "short": short_name(parish),
            "col": pos[0] if pos else None, "row": pos[1] if pos else None,
            "median_ppm": med, "n": n,
            "other_median_ppm": med_other, "other_n": n_other,
            "change_3m": own[90], "change_6m": own[180],
            "change_12m": own[365] if own[365] is not None else ine12,
            "change_12m_source": "apscrap" if own[365] is not None else ("ine" if ine12 is not None else None),
            "gross_yield": gross_yield,
            "median_days": round(_median(z["dom"])) if z["dom"] else None,
            "new_7d": z["new7"],
            "ine_latest": ({"value": ine_pts[-1][1], "period_end": str(ine_pts[-1][0])} if ine_pts else None),
            "spark": spark,
            "reading": _reading(gross_yield, n, n_other),
        })
    history_days = (max((r["day"] for r in agg), default=dt.date.today())
                    - min((r["day"] for r in agg), default=dt.date.today())).days
    return {"mode": mode, "zones": out, "history_days": history_days, "min_n": MIN_N}


def _weekly(series):
    """Weekly medians (Monday-based) from daily medians with n >= MIN_N."""
    weeks = defaultdict(list)
    for d, v, n in series:
        if n >= MIN_N:
            weeks[d - dt.timedelta(days=d.weekday())].append(v)
    return [[str(w), round(statistics.median(v), 2)] for w, v in sorted(weeks.items())]


def _reading(gross_yield, n, n_other) -> str:
    if gross_yield is None:
        if n < MIN_N:
            return f"Só {n} anúncios ativos aqui: poucos para uma mediana fiável. Compara com o concelho."
        return "Sem anúncios suficientes do outro lado (venda ou arrendamento) para comparar comprar e arrendar."
    if gross_yield >= 5.4:
        return (f"Um ano de renda vale {gross_yield:.1f}% do preço de compra, alto para a zona: "
                "aqui comprar tende a compensar mais do que arrendar.")
    if gross_yield <= 4.2:
        return (f"Um ano de renda vale só {gross_yield:.1f}% do preço: as casas estão caras face às rendas, "
                "e arrendar pesa menos.")
    return f"Um ano de renda vale {gross_yield:.1f}% do preço, perto da média: comprar e arrendar equilibram-se."


def council_medians(mode: str, councils: list[str], property_type: str = "flat") -> dict:
    with connect() as conn:
        rows = conn.execute(
            """SELECT council, count(*) AS n,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm) AS ppm
                 FROM mv_deals WHERE transaction_type = %s AND council = ANY(%s) AND property_type = %s
                GROUP BY council""", (mode, councils, property_type)).fetchall()
    return {r["council"]: {"n": r["n"], "median_ppm": round(float(r["ppm"]), 2)} for r in rows}


# -- what changed (the market diary feed) --------------------------------------

def events(mode: str, councils: list[str], shortlisted: set[int] | None = None, limit: int = 8) -> list[dict]:
    """Recent things worth a line: drops on saved adverts, new adverts below the
    median, the biggest weekly moves once there are two weeks of history."""
    shortlisted = shortlisted or set()
    out = []
    with connect() as conn:
        drops = conn.execute(
            """SELECT c.listing_id, c.changed_at, c.prev_price, c.new_price, d.typology, d.parish,
                      d.council, d.root_id
                 FROM v_price_changes c JOIN mv_deals d ON d.id = c.listing_id
                WHERE c.direction = 'drop' AND c.changed_at > now() - interval '7 days'
                  AND d.transaction_type = %s AND d.council = ANY(%s)
                ORDER BY c.changed_at DESC""", (mode, councils)).fetchall()
        fresh = conn.execute(
            """SELECT parish, council, typology, count(*) AS n
                 FROM mv_deals
                WHERE transaction_type = %s AND council = ANY(%s)
                  AND first_seen_at > now() - interval '3 days' AND deal_score > 5
                GROUP BY 1, 2, 3 HAVING count(*) >= 2
                ORDER BY n DESC LIMIT 4""", (mode, councils)).fetchall()
        weekly = conn.execute(
            """WITH w AS (
                   SELECT parish, council, day, median_ppm, n FROM market_agg_daily
                    WHERE transaction_type = %s AND council = ANY(%s) AND parish <> ''
                      AND property_type = 'flat' AND typology = '' AND n >= 8
                      AND day > current_date - 15)
               SELECT parish, council,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY median_ppm)
                          FILTER (WHERE day > current_date - 8) AS now_ppm,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY median_ppm)
                          FILTER (WHERE day <= current_date - 8) AS before_ppm
                 FROM w GROUP BY 1, 2""", (mode, councils)).fetchall()

    unit = "€/mês" if mode == "rent" else "€"
    saved_drops = [d for d in drops if d["listing_id"] in shortlisted or d["root_id"] in shortlisted]
    for d in saved_drops[:3]:
        out.append({"kind": "down", "texto": f"{d['typology'] or 'Imóvel'} em {short_name(d['parish'] or d['council'])} "
                    f"da shortlist baixou {fmt_eur(d['prev_price'] - d['new_price'])}{'' if mode == 'sale' else '/mês'}",
                    "when": d["changed_at"].isoformat(), "listing_id": d["listing_id"]})
    for f in fresh:
        out.append({"kind": "new", "texto": f"{f['n']} novos {f['typology'] or ''} em {short_name(f['parish'] or f['council'])} "
                    "abaixo da mediana", "when": dt.datetime.now(dt.timezone.utc).isoformat()})
    moves = []
    for w in weekly:
        if w["now_ppm"] and w["before_ppm"]:
            moves.append(((w["now_ppm"] / w["before_ppm"] - 1) * 100, w))
    for pct, w in sorted(moves, key=lambda m: -abs(m[0]))[:2]:
        if abs(pct) >= 0.5:
            out.append({"kind": "up" if pct > 0 else "down",
                        "texto": f"{short_name(w['parish'])}: mediana {'+' if pct > 0 else '−'}{abs(pct):.1f}% numa semana",
                        "when": dt.datetime.now(dt.timezone.utc).isoformat()})
    if len(drops) and not saved_drops:
        out.append({"kind": "down", "texto": f"{len(drops)} baixas de preço nos últimos 7 dias nas zonas que seguem",
                    "when": drops[0]["changed_at"].isoformat()})
    _ = unit
    return out[:limit]


def fmt_eur(v) -> str:
    return f"{float(v):,.0f} €".replace(",", " ")
