import psycopg
from psycopg.rows import dict_row

from .config import DSN, SQL_DIR


def connect():
    return psycopg.connect(DSN, row_factory=dict_row, autocommit=False)


def migrate() -> None:
    """Apply every .sql file in sql/ in filename order. All statements are idempotent."""
    with connect() as conn:
        for path in sorted(SQL_DIR.glob("*.sql")):
            conn.execute(path.read_text())
            print(f"applied {path.name}")
        conn.commit()


def start_run(conn, source: str, params: dict) -> int:
    row = conn.execute(
        "INSERT INTO crawl_runs (source, params) VALUES (%s, %s) RETURNING id",
        (source, psycopg.types.json.Jsonb(params)),
    ).fetchone()
    conn.commit()
    return row["id"]


def finish_run(conn, run_id: int, pages: int, items: int, error: str | None = None,
               status: str | None = None) -> None:
    """status: 'ok', 'incomplete' (some pages failed, most answered) or
    'failed'; by default failed when there is an error."""
    conn.execute(
        """UPDATE crawl_runs
              SET finished_at = now(), pages_fetched = %s, items_seen = %s,
                  status = %s, error = %s
            WHERE id = %s""",
        (pages, items, status or ("failed" if error else "ok"), error, run_id),
    )
    conn.commit()


def save_raw_page(conn, run_id, source, kind, url, status, payload) -> int:
    row = conn.execute(
        """INSERT INTO raw_pages (run_id, source, kind, url, http_status, payload)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (run_id, source, kind, url, status, psycopg.types.json.Jsonb(payload)),
    ).fetchone()
    conn.commit()
    return row["id"]


def prune_raw_search(keep_days: int) -> dict:
    """Delete raw search pages that parsed cleanly and are past retention.
    Pages that failed to parse are kept, because those are the ones a parser
    fix needs to replay.

    Advert pages that RE/MAX and KW re-read every week ('listing') go the same
    way, except the newest copy of each advert, which a replay needs. Imovirtual
    detail pages are read once and kept."""
    with connect() as conn:
        n = conn.execute(
            """DELETE FROM raw_pages
                WHERE kind = 'search'
                  AND parsed_at IS NOT NULL AND parse_error IS NULL
                  AND fetched_at < now() - make_interval(days => %s)""",
            (keep_days,),
        ).rowcount
        m = conn.execute(
            """DELETE FROM raw_pages r
                WHERE r.kind = 'listing'
                  AND r.parsed_at IS NOT NULL AND r.parse_error IS NULL
                  AND r.fetched_at < now() - make_interval(days => %s)
                  AND EXISTS (SELECT 1 FROM raw_pages n
                               WHERE n.source = r.source AND n.kind = 'listing'
                                 AND n.url = r.url AND n.id > r.id)""",
            (keep_days,),
        ).rowcount
        conn.commit()
    return {"deleted": n, "deleted_listing_copies": m, "keep_days": keep_days}
