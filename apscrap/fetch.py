"""Stage 1: pull pages from the portal into raw_pages. No parsing happens here."""
import logging

from .db import connect, finish_run, save_raw_page, start_run
from .sources import SOURCES
from .sources.base import Fetcher

log = logging.getLogger(__name__)


def crawl_search(
    source_name: str,
    transaction: str,
    property_type: str,
    location: str,
    max_pages: int | None = None,
) -> dict:
    source = SOURCES[source_name]
    fetcher = Fetcher()
    params = {
        "transaction": transaction,
        "property_type": property_type,
        "location": location,
        "max_pages": max_pages,
    }
    pages = items = 0
    error = None

    with connect() as conn:
        run_id = start_run(conn, source_name, params)
        try:
            page = 1
            total_pages = 1
            while page <= total_pages:
                url = source.search_url(transaction, property_type, location, page)
                resp = fetcher.get(url)
                payload = source.search_payload(resp.text) if resp.status_code == 200 else None

                save_raw_page(conn, run_id, source_name, "search", url, resp.status_code, payload)
                pages += 1

                if payload is None:
                    log.warning("no payload at %s (HTTP %s), stopping", url, resp.status_code)
                    break

                if page == 1:
                    total_pages = source.total_pages(payload)
                    if max_pages:
                        total_pages = min(total_pages, max_pages)
                    log.info("%s: %s adverts over %s pages",
                             location, source.total_items(payload), total_pages)

                items += len(payload.get("items", []))
                log.info("page %s/%s  (%s adverts so far)", page, total_pages, items)
                page += 1

        except Exception as exc:  # noqa: BLE001 - recorded on the run, then re-raised
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            finish_run(conn, run_id, pages, items, error)
            fetcher.close()

    return {"run_id": run_id, "pages": pages, "items": items}


def crawl_details(source_name: str, limit: int = 100) -> dict:
    """Fetch detail pages for listings we have never enriched. Adds lat/lon,
    deposit, energy certificate and build year."""
    source = SOURCES[source_name]
    fetcher = Fetcher()
    fetched = 0
    error = None

    with connect() as conn:
        run_id = start_run(conn, source_name, {"kind": "detail", "limit": limit})
        try:
            rows = conn.execute(
                """SELECT id, url FROM listings
                    WHERE source = %s AND detail_fetched_at IS NULL
                    -- Rentals first: they are what the site and the alert read,
                    -- and the coordinates are what the commute time needs.
                    ORDER BY (transaction_type = 'rent') DESC, first_seen_at DESC
                    LIMIT %s""",
                (source_name, limit),
            ).fetchall()
            log.info("%s listings need a detail fetch", len(rows))

            for row in rows:
                resp = fetcher.get(row["url"])
                payload = source.detail_payload(resp.text) if resp.status_code == 200 else None
                save_raw_page(conn, run_id, source_name, "detail",
                              row["url"], resp.status_code, payload)
                # Mark it done either way. A 404 means the advert is gone, and
                # retrying it every night forever would be pointless traffic.
                conn.execute(
                    "UPDATE listings SET detail_fetched_at = now() WHERE id = %s", (row["id"],)
                )
                conn.commit()
                fetched += 1
                if fetched % 20 == 0:
                    log.info("detail %s/%s", fetched, len(rows))

        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            finish_run(conn, run_id, fetched, fetched, error)
            fetcher.close()

    return {"run_id": run_id, "fetched": fetched}
