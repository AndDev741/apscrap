"""Stage 1: pull pages from the portal into raw_pages. No parsing happens here."""
import logging

from .db import connect, finish_run, save_raw_page, start_run
from .sources import SOURCES
from .sources.base import CrawlContext, Fetcher

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
                    if page < total_pages or page == 1:
                        # Recorded on the run (status failed) so that
                        # lifecycle.py does not read the unseen adverts as gone.
                        error = f"stopped at page {page} of {total_pages} (HTTP {resp.status_code})"
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

    return {"run_id": run_id, "pages": pages, "items": items, **({"error": error} if error else {})}


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
            # What is left of the budget refreshes active rentals fetched before
            # photos and extras were kept, newest first.
            if len(rows) < limit:
                rows += conn.execute(
                    """SELECT id, url FROM listings
                        WHERE source = %s AND detail_fetched_at IS NOT NULL
                          AND image_urls IS NULL AND transaction_type = 'rent'
                          AND last_seen_at > now() - interval '3 days'
                        ORDER BY last_seen_at DESC, id DESC
                        LIMIT %s""",
                    (source_name, limit - len(rows)),
                ).fetchall()
            log.info("%s listings need a detail fetch", len(rows))

            for row in rows:
                resp = fetcher.get(row["url"])
                payload = source.detail_payload(resp.text) if resp.status_code == 200 else None
                save_raw_page(conn, run_id, source_name, "detail",
                              row["url"], resp.status_code, payload)
                if resp.status_code in (404, 410):
                    from .lifecycle import mark_removed
                    mark_removed(conn, row["id"])
                # Mark it done either way. A 404 means the advert is gone, and
                # retrying it every night forever would be pointless traffic.
                conn.execute(
                    """UPDATE listings SET detail_fetched_at = now(),
                              image_urls = CASE WHEN %s THEN COALESCE(image_urls, '{}') ELSE image_urls END
                        WHERE id = %s""",
                    (payload is None, row["id"]),
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


def crawl_source(source_name: str, councils: list[str], detail_limit: int, **kw) -> dict:
    """One night of a portal other than Imovirtual: the source walks its own
    site (sitemaps, search pages, APIs) and stores what it reads in raw_pages.
    Problems that leave the crawl incomplete mark the run failed, so the
    lifecycle step leaves that portal's adverts alone tonight."""
    source = SOURCES[source_name]
    fetcher = Fetcher(delay=source.delay)
    error = None
    with connect() as conn:
        run_id = start_run(conn, source_name, {"councils": councils, "detail_limit": detail_limit})
        ctx = CrawlContext(fetcher=fetcher, conn=conn, run_id=run_id, source=source_name,
                           councils=councils, detail_limit=detail_limit)
        try:
            source.crawl(ctx, **kw)
            if ctx.problems:
                error = "; ".join(ctx.problems)[:1000]
        except Exception as exc:  # noqa: BLE001 - recorded on the run, then re-raised
            conn.rollback()
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            finish_run(conn, run_id, ctx.pages, ctx.items, error)
            fetcher.close()
    return {"run_id": run_id, "pages": ctx.pages, "items": ctx.items, **({"error": error} if error else {})}
