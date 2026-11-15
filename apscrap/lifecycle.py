"""When an advert left the market.

Portals do not say "rented" (Imovirtual, RE/MAX and Casa Sapo just drop the
advert), so an advert is gone once its portal has been crawled successfully a
few times without it. ERA and KW do say it, and their parsers set
gone_reason = 'rented' directly.

The grace period is per portal and counts from the newest observation of that
portal, never from now(): if a crawl fails for a week, nothing is marked gone
meanwhile, and a crawl that failed tonight leaves that portal alone until it
works again. An advert marked gone that turns up again is active again.
"""
import logging

from .db import connect

log = logging.getLogger(__name__)

# Hours since the portal's newest observation before a missing advert counts as
# gone. 60 h tolerates two missed nights, which is what the views used to
# allow (three days) and keeps the false "gone" rate under 2%.
GRACE_HOURS = {"imovirtual": 60, "remax": 60, "era": 60, "kw": 60, "casasapo": 60}
DEFAULT_GRACE_HOURS = 60


def _healthy_sources(conn) -> dict[str, bool]:
    """Sources with a search crawl in the last 30 hours, and whether all of
    those crawls finished cleanly."""
    rows = conn.execute(
        """SELECT source, bool_and(status = 'ok') AS ok
             FROM crawl_runs
            WHERE started_at > now() - interval '30 hours'
              AND COALESCE(params->>'kind', 'search') = 'search'
            GROUP BY source"""
    ).fetchall()
    return {r["source"]: r["ok"] for r in rows}


def run() -> dict:
    out = {"gone": {}, "back": 0, "skipped": []}
    with connect() as conn:
        # Back on the portal: active again, whatever the reason was, unless the
        # portal itself still says rented (the parser keeps that up to date).
        out["back"] = conn.execute(
            """UPDATE listings SET gone_at = NULL, gone_reason = NULL
                WHERE gone_at IS NOT NULL AND gone_reason <> 'rented'
                  AND last_seen_at > gone_at"""
        ).rowcount

        health = _healthy_sources(conn)
        sources = [r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM listings WHERE gone_at IS NULL").fetchall()]
        for source in sources:
            if not health.get(source):
                # No crawl in 30 h, or one that failed: missing tonight means
                # nothing.
                out["skipped"].append(source)
                continue
            grace = GRACE_HOURS.get(source, DEFAULT_GRACE_HOURS)
            n = conn.execute(
                """WITH newest AS (SELECT max(last_seen_at) AS t FROM listings WHERE source = %(s)s)
                   UPDATE listings l
                      SET gone_at = l.last_seen_at,
                          gone_reason = CASE WHEN EXISTS (SELECT 1 FROM listings r WHERE r.relist_of = l.id)
                                             THEN 'relisted' ELSE 'missing' END
                     FROM newest
                    WHERE l.source = %(s)s AND l.gone_at IS NULL
                      AND l.last_seen_at < newest.t - make_interval(hours => %(g)s)""",
                {"s": source, "g": grace},
            ).rowcount
            out["gone"][source] = n
        # A relist found after the advert was already marked gone.
        conn.execute(
            """UPDATE listings l SET gone_reason = 'relisted'
                WHERE l.gone_reason = 'missing'
                  AND EXISTS (SELECT 1 FROM listings r WHERE r.relist_of = l.id)"""
        )
        conn.commit()
    log.info("lifecycle: %s", out)
    return out


def mark_removed(conn, listing_id: int) -> None:
    """A detail page answered 404/410. Only trusted when the search crawl has
    not seen the advert for a day either: a fresh advert can 404 for a moment
    while the portal publishes it."""
    conn.execute(
        """UPDATE listings
              SET gone_at = COALESCE(gone_at, last_seen_at), gone_reason = 'removed'
            WHERE id = %s AND last_seen_at < now() - interval '1 day'""",
        (listing_id,),
    )
