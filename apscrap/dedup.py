"""The same flat on several portals.

Agencies post one flat on Imovirtual, their own site and Casa Sapo at once.
Counted three times it would weigh three times in every median, and show up
three times in the list. Here live adverts from different portals are grouped
when they agree on transaction, type, typology and council, and on

- price, within 3%,
- area, within 5% (or 3 m²): portals round, and some agencies swap net and
  gross area,
- place: within 400 m when both have coordinates, else the same parish.

When the advertisers differ (an ERA office on one portal, a Century 21 one on
the other) the bar is higher: price within 1%, area within 3% (or 2 m²) and
within 200 m. Two agencies with the same flat happens, but so do two similar
flats in the same street.

A group holds at most one advert per portal, built greedily from the closest
pairs, so two similar flats in one building on the same portal are never
merged with each other. One advert per group is shown (Imovirtual's when there
is one, since it has the longest history, else the oldest), and the others
get dup_of pointing at it. The whole thing is recomputed every night over live
adverts, so a link disappears when either side does. Gone adverts keep the
link they had.

Same-portal reposts over time are a different problem, handled by relist.py.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict

from .db import connect
from .geo import fold

log = logging.getLogger(__name__)

PRICE_TOL = 0.03
AREA_TOL = 0.05
AREA_TOL_ABS = 3.0
MAX_DISTANCE_M = 400
PRIORITY = {"imovirtual": 0, "remax": 1, "casasapo": 2, "era": 3, "kw": 4}
STRICT = {"price": 0.01, "area": 0.03, "area_abs": 2.0, "metres": 200}
# Networks whose offices go by "RE/MAX Siimgroup", "Remax Expo", "ERA
# Laranjeiras", "KW LEAD"... The portal itself counts as its network.
BRANDS = [("remax", ("remax", "re max")), ("era", ("era",)), ("kw", ("kw", "keller williams")),
          ("century21", ("century21", "century 21", "c21")), ("zome", ("zome",)), ("iad", ("iad",)),
          ("decisoes", ("decisoes e solucoes",)), ("coldwell", ("coldwell banker",))]
SOURCE_BRAND = {"remax": "remax", "era": "era", "kw": "kw"}


def advertiser(source: str, agency: str | None, private: bool | None) -> str:
    """Who placed the advert, coarse enough to compare across portals."""
    if source in SOURCE_BRAND:
        return SOURCE_BRAND[source]
    if private:
        return "particular"
    f = " " + fold(agency) + " "
    for key, words in BRANDS:
        if any(f" {w} " in f for w in words):
            return key
    return fold(agency) or "?"


def _same_advertiser(a: str, b: str) -> bool:
    if a == b:
        return True
    ta, tb = set(a.split()) - {"lda", "imobiliaria", "mediacao", "unipessoal", "real", "estate"}, set(b.split())
    return bool(ta) and len(ta & tb) / len(ta | tb) >= 0.5

LIVE = """
SELECT id, source, transaction_type, property_type, typology, council, parish,
       area_sqm::float AS area, price_eur::float AS price, latitude, longitude, first_seen_at,
       agency_name, is_private_owner
  FROM v_listing_current
 WHERE is_active AND is_plausible AND NOT is_junk AND NOT is_midstay
   AND transaction_type = 'rent'
"""


def _metres(a, b) -> float:
    dlat = math.radians(b["latitude"] - a["latitude"])
    dlon = math.radians(b["longitude"] - a["longitude"]) * math.cos(
        math.radians((a["latitude"] + b["latitude"]) / 2))
    return 6371000 * math.hypot(dlat, dlon)


def match(a: dict, b: dict) -> float | None:
    """A score (lower is closer) when a and b look like the same flat."""
    if a["source"] == b["source"]:
        return None
    same = _same_advertiser(advertiser(a["source"], a["agency_name"], a["is_private_owner"]),
                            advertiser(b["source"], b["agency_name"], b["is_private_owner"]))
    price_tol = PRICE_TOL if same else STRICT["price"]
    area_tol, area_abs = (AREA_TOL, AREA_TOL_ABS) if same else (STRICT["area"], STRICT["area_abs"])
    metres = MAX_DISTANCE_M if same else STRICT["metres"]
    pd = abs(a["price"] - b["price"]) / max(a["price"], b["price"])
    if pd > price_tol:
        return None
    ad = abs(a["area"] - b["area"])
    if ad > area_abs and ad / max(a["area"], b["area"]) > area_tol:
        return None
    if None not in (a["latitude"], b["latitude"]):
        dist = _metres(a, b)
        if dist > metres:
            return None
        place = dist / MAX_DISTANCE_M
    elif same and a["parish"] and a["parish"] == b["parish"]:
        place = 0.5
    else:
        return None
    return pd / PRICE_TOL + (ad / max(a["area"], b["area"])) / AREA_TOL + place + (0 if same else 1)


def group(rows: list[dict]) -> list[list[dict]]:
    """Clusters of the same flat, at most one advert per portal each."""
    buckets = defaultdict(list)
    for r in rows:
        if r["typology"] and r["council"] and r["area"] and r["price"]:
            buckets[(r["transaction_type"], r["property_type"], r["typology"], r["council"])].append(r)
    pairs = []
    for items in buckets.values():
        if len({r["source"] for r in items}) < 2:
            continue
        items.sort(key=lambda r: r["price"])
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if b["price"] > a["price"] / (1 - PRICE_TOL):
                    break
                s = match(a, b)
                if s is not None:
                    pairs.append((s, a["id"], b["id"]))
    pairs.sort()
    by_id = {r["id"]: r for r in rows}
    cluster = {}            # id -> cluster key
    members = {}            # key -> list of ids
    for _s, x, y in pairs:
        cx, cy = cluster.get(x, x), cluster.get(y, y)
        if cx == cy:
            continue
        mx, my = members.get(cx, [x]), members.get(cy, [y])
        if {by_id[i]["source"] for i in mx} & {by_id[i]["source"] for i in my}:
            continue
        merged = mx + my
        members[cx] = merged
        members.pop(cy, None)
        for i in merged:
            cluster[i] = cx
    return [[by_id[i] for i in ids] for ids in members.values() if len(ids) > 1]


def run() -> dict:
    with connect() as conn:
        rows = conn.execute(LIVE).fetchall()
        clusters = group(rows)
        links = {}
        for c in clusters:
            keep = min(c, key=lambda r: (PRIORITY.get(r["source"], 9), r["first_seen_at"], r["id"]))
            for r in c:
                if r["id"] != keep["id"]:
                    links[r["id"]] = keep["id"]
        live_ids = [r["id"] for r in rows]
        # Recompute for every live advert: clear, then set.
        conn.execute(
            """UPDATE listings SET dup_of = NULL
                WHERE id = ANY(%s) AND dup_of IS NOT NULL AND NOT (id = ANY(%s))""",
            (live_ids, list(links)))
        if links:
            conn.execute(
                """UPDATE listings l SET dup_of = x.keep, dup_checked_at = now()
                     FROM unnest(%s::bigint[], %s::bigint[]) AS x(id, keep)
                    WHERE l.id = x.id AND l.dup_of IS DISTINCT FROM x.keep""",
                (list(links), list(links.values())))
        conn.execute("UPDATE listings SET dup_checked_at = now() WHERE id = ANY(%s)", (live_ids,))
        conn.commit()
    by_pair = defaultdict(int)
    for c in clusters:
        by_pair["+".join(sorted(r["source"] for r in c))] += 1
    out = {"groups": len(clusters), "copies": len(links), "by_portals": dict(by_pair)}
    log.info("dedup: %s", out)
    return out
