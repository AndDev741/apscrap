"""Stage 2: drain unparsed raw_pages into listings and listing_snapshots.

raw_pages is the queue. A row with parsed_at IS NULL is unprocessed work, which
gives us at-least-once semantics and full replay without running a broker: wipe
the normalised tables, reset parsed_at, and every page we ever fetched flows
through again.
"""
import logging

from .db import connect
from .sources import SOURCES

log = logging.getLogger(__name__)

UPSERT_LISTING = """
INSERT INTO listings (
    source, external_id, url, transaction_type, property_type, typology,
    typology_inferred, rooms_total, area_sqm, floor, district, council, parish, neighborhood,
    street, is_private_owner, agency_name, listed_at, thumbnail_url,
    first_seen_at, last_seen_at
) VALUES (
    %(source)s, %(external_id)s, %(url)s, %(transaction_type)s, %(property_type)s,
    %(typology)s, %(typology_inferred)s, %(rooms_total)s, %(area_sqm)s, %(floor)s, %(district)s,
    %(council)s, %(parish)s, %(neighborhood)s, %(street)s, %(is_private_owner)s,
    %(agency_name)s, %(listed_at)s, %(thumbnail_url)s,
    %(fetched_at)s, %(fetched_at)s
)
ON CONFLICT (source, external_id) DO UPDATE SET
    -- Seen-dates come from when the page was fetched, never from now(), so a
    -- replay of old pages cannot make a vanished advert look active today.
    first_seen_at    = LEAST(listings.first_seen_at, EXCLUDED.first_seen_at),
    last_seen_at     = GREATEST(listings.last_seen_at, EXCLUDED.last_seen_at),
    url              = EXCLUDED.url,
    -- Refreshed on every pass so that clearing parsed_at and replaying every
    -- stored page genuinely rebuilds the table, parser fixes included.
    transaction_type = COALESCE(EXCLUDED.transaction_type, listings.transaction_type),
    property_type    = COALESCE(EXCLUDED.property_type,    listings.property_type),
    typology         = COALESCE(EXCLUDED.typology,         listings.typology),
    typology_inferred = EXCLUDED.typology_inferred,
    rooms_total      = COALESCE(EXCLUDED.rooms_total,      listings.rooms_total),
    area_sqm         = COALESCE(EXCLUDED.area_sqm,         listings.area_sqm),
    -- floor may have come from the richer detail page; don't clobber it
    floor            = COALESCE(listings.floor,            EXCLUDED.floor),
    district         = COALESCE(EXCLUDED.district,         listings.district),
    council          = COALESCE(EXCLUDED.council,          listings.council),
    parish           = COALESCE(EXCLUDED.parish,           listings.parish),
    neighborhood     = COALESCE(EXCLUDED.neighborhood,     listings.neighborhood),
    street           = COALESCE(EXCLUDED.street,           listings.street),
    is_private_owner = COALESCE(EXCLUDED.is_private_owner, listings.is_private_owner),
    agency_name      = COALESCE(EXCLUDED.agency_name,      listings.agency_name),
    listed_at        = COALESCE(listings.listed_at,        EXCLUDED.listed_at),
    thumbnail_url    = COALESCE(EXCLUDED.thumbnail_url,    listings.thumbnail_url)
RETURNING id
"""

INSERT_SNAPSHOT = """
INSERT INTO listing_snapshots (
    listing_id, price_eur, price_per_sqm, title, is_promoted, pushed_up_at, raw_page_id,
    observed_at, observed_date
) VALUES (
    %(listing_id)s, %(price_eur)s, %(price_per_sqm)s, %(title)s,
    %(is_promoted)s, %(pushed_up_at)s, %(raw_page_id)s,
    %(fetched_at)s, (%(fetched_at)s AT TIME ZONE 'Europe/Lisbon')::date
)
ON CONFLICT (listing_id, observed_date) DO UPDATE SET
    price_eur     = EXCLUDED.price_eur,
    price_per_sqm = EXCLUDED.price_per_sqm,
    title         = EXCLUDED.title,
    observed_at   = EXCLUDED.observed_at
"""

UPDATE_DETAIL = """
UPDATE listings SET
    latitude    = COALESCE(%(latitude)s,    latitude),
    longitude   = COALESCE(%(longitude)s,   longitude),
    build_year  = COALESCE(%(build_year)s,  build_year),
    energy_cert = COALESCE(%(energy_cert)s, energy_cert),
    condition   = COALESCE(%(condition)s,   condition),
    deposit_eur = COALESCE(%(deposit_eur)s, deposit_eur),
    floor       = COALESCE(%(floor)s,       floor),
    -- so a replay into an empty table does not queue every detail page again
    detail_fetched_at = COALESCE(detail_fetched_at, %(fetched_at)s)
WHERE source = %(source)s AND url = %(url)s
"""


def process_pending(batch: int = 500) -> dict:
    stats = {"pages": 0, "listings": 0, "snapshots": 0, "errors": 0}

    with connect() as conn:
        while True:
            pages = conn.execute(
                """SELECT id, source, kind, url, http_status, payload, fetched_at
                     FROM raw_pages
                    WHERE parsed_at IS NULL
                    ORDER BY id
                    LIMIT %s""",
                (batch,),
            ).fetchall()
            if not pages:
                break

            for page in pages:
                try:
                    _process_one(conn, page, stats)
                    conn.execute(
                        "UPDATE raw_pages SET parsed_at = now(), parse_error = NULL WHERE id = %s",
                        (page["id"],),
                    )
                except Exception as exc:  # noqa: BLE001 - one bad page must not stop the drain
                    conn.rollback()
                    stats["errors"] += 1
                    log.exception("raw_page %s failed", page["id"])
                    conn.execute(
                        "UPDATE raw_pages SET parsed_at = now(), parse_error = %s WHERE id = %s",
                        (f"{type(exc).__name__}: {exc}", page["id"]),
                    )
                conn.commit()
                stats["pages"] += 1

    return stats


def _process_one(conn, page, stats) -> None:
    payload = page["payload"]
    if not payload:
        return
    source = SOURCES[page["source"]]

    if page["kind"] == "search":
        for listing, snapshot in source.parse_search_items(payload):
            listing["fetched_at"] = page["fetched_at"]
            listing_id = conn.execute(UPSERT_LISTING, listing).fetchone()["id"]
            snapshot |= {"listing_id": listing_id, "raw_page_id": page["id"],
                         "fetched_at": page["fetched_at"]}
            conn.execute(INSERT_SNAPSHOT, snapshot)
            stats["listings"] += 1
            stats["snapshots"] += 1

    elif page["kind"] == "detail":
        attrs = source.parse_detail(payload)
        attrs |= {"source": page["source"], "url": page["url"], "fetched_at": page["fetched_at"]}
        conn.execute(UPDATE_DETAIL, attrs)
