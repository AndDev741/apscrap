"""Catch adverts that were withdrawn and posted again (IM-3).

An agency that deletes and reposts an advert resets the portal's "published
on" date, so days on market starts again from zero. Here a new advert is
matched against older ones on the same flat: council, parish, typology,
transaction, area within 2%, position within 50 m when both are known, and a
fuzzy match on the title. The agency may differ.

The match is written to listings.relist_of (the older advert), and the chain
fields (relist_root, relist_count, chain_first_seen) are refreshed for every
listing in a chain, so the views read them without walking anything.
"""
import logging
import math
import re
from difflib import SequenceMatcher

from .db import connect

log = logging.getLogger(__name__)

AREA_TOLERANCE = 0.02
MAX_DISTANCE_M = 50
TITLE_MIN_RATIO = 0.55
# An old advert counts if it was last seen at most this many days before the
# new one appeared (or overlaps it by a couple of days).
GAP_DAYS = 120

_WORDS = re.compile(r"[^\w]+", re.U)


def _norm(title: str | None) -> str:
    return " ".join(_WORDS.split((title or "").lower())).strip()


def _metres(a_lat, a_lon, b_lat, b_lon) -> float:
    dlat = math.radians(b_lat - a_lat)
    dlon = math.radians(b_lon - a_lon) * math.cos(math.radians((a_lat + b_lat) / 2))
    return 6371000 * math.hypot(dlat, dlon)


CANDIDATES = """
SELECT n.id AS new_id, o.id AS old_id,
       n.latitude AS n_lat, n.longitude AS n_lon, o.latitude AS o_lat, o.longitude AS o_lon,
       n.area_sqm AS n_area, o.area_sqm AS o_area,
       (SELECT title FROM listing_snapshots WHERE listing_id = n.id ORDER BY observed_at DESC LIMIT 1) AS n_title,
       (SELECT title FROM listing_snapshots WHERE listing_id = o.id ORDER BY observed_at DESC LIMIT 1) AS o_title
FROM listings n
JOIN listings o
  ON o.id <> n.id
 AND o.source = n.source
 AND o.transaction_type = n.transaction_type
 AND o.council = n.council
 AND o.parish IS NOT DISTINCT FROM n.parish
 AND o.typology = n.typology
 AND o.area_sqm BETWEEN n.area_sqm * (1 - %(tol)s) AND n.area_sqm * (1 + %(tol)s)
 AND o.first_seen_at < n.first_seen_at
 AND o.last_seen_at BETWEEN n.first_seen_at - make_interval(days => %(gap)s)
                        AND n.first_seen_at + interval '2 days'
 -- the older advert must have stopped: still live means two adverts, not a relist
 AND o.last_seen_at < n.last_seen_at - interval '1 day'
WHERE n.relist_checked_at IS NULL
  AND n.relist_of IS NULL
  AND n.area_sqm IS NOT NULL AND n.typology IS NOT NULL
"""


def detect() -> dict:
    """Match every unchecked advert, then refresh the chain fields."""
    matched = checked = 0
    with connect() as conn:
        rows = conn.execute(CANDIDATES, {"tol": AREA_TOLERANCE, "gap": GAP_DAYS}).fetchall()
        best: dict[int, tuple[float, int]] = {}
        for r in rows:
            if None not in (r["n_lat"], r["o_lat"]):
                if _metres(r["n_lat"], r["n_lon"], r["o_lat"], r["o_lon"]) > MAX_DISTANCE_M:
                    continue
            ratio = SequenceMatcher(None, _norm(r["n_title"]), _norm(r["o_title"])).ratio()
            if ratio < TITLE_MIN_RATIO:
                continue
            # Prefer the closest title, then the most recent older advert.
            score = (ratio, r["old_id"])
            if r["new_id"] not in best or score > best[r["new_id"]]:
                best[r["new_id"]] = score

        for new_id, (_ratio, old_id) in best.items():
            conn.execute("UPDATE listings SET relist_of = %s WHERE id = %s AND relist_of IS NULL",
                         (old_id, new_id))
            matched += 1

        # Anything that has been around for a week without a match is settled.
        checked = conn.execute(
            """UPDATE listings SET relist_checked_at = now()
                WHERE relist_checked_at IS NULL
                  AND (relist_of IS NOT NULL OR first_seen_at < now() - interval '7 days')"""
        ).rowcount
        _refresh_chains(conn)
        conn.commit()
    log.info("relist: %s matched, %s settled", matched, checked)
    return {"matched": matched, "settled": checked}


def _refresh_chains(conn) -> None:
    conn.execute(
        """WITH RECURSIVE up AS (
               SELECT id AS listing_id, id AS ancestor, relist_of, 0 AS depth
                 FROM listings
                WHERE relist_of IS NOT NULL
                   OR id IN (SELECT relist_of FROM listings WHERE relist_of IS NOT NULL)
               UNION ALL
               SELECT up.listing_id, l.id, l.relist_of, up.depth + 1
                 FROM up JOIN listings l ON l.id = up.relist_of
                WHERE up.depth < 20
           ),
           chain AS (
               SELECT u.listing_id,
                      (array_agg(u.ancestor ORDER BY u.depth DESC))[1] AS root,
                      max(u.depth) AS n,
                      min(COALESCE(l.listed_at, l.first_seen_at)) AS first_seen
                 FROM up u JOIN listings l ON l.id = u.ancestor
                GROUP BY u.listing_id
           )
           UPDATE listings l
              SET relist_root = c.root, relist_count = c.n, chain_first_seen = c.first_seen
             FROM chain c
            WHERE l.id = c.listing_id
              AND (l.relist_root, l.relist_count, l.chain_first_seen)
                  IS DISTINCT FROM (c.root, c.n, c.first_seen)"""
    )
