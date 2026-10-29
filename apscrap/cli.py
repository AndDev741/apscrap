import argparse
import logging
import sys

from . import alert, commute, db, export, fetch, parse
from .config import DETAIL_LIMIT, RAW_SEARCH_KEEP_DAYS

# Where you might actually move. Edit this list, it drives the daily run.
# On Imovirtual "lisboa" is the whole district, so Odivelas, Loures, Amadora and
# the rest of the commuter belt are already inside these.
DEFAULT_TARGETS = [
    ("rent", "flat", "lisboa"),
    ("rent", "house", "lisboa"),
    ("sale", "flat", "lisboa"),
]


def _log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="apscrap", description="Portuguese apartment price tracker")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="create tables and views")

    c = sub.add_parser("crawl", help="fetch search pages into raw_pages")
    c.add_argument("--source", default="imovirtual")
    c.add_argument("--transaction", choices=["rent", "sale"])
    c.add_argument("--type", dest="property_type", default="flat", choices=["flat", "house"])
    c.add_argument("--location", help="portal location slug, e.g. lisboa or porto")
    c.add_argument("--max-pages", type=int, help="cap pages, handy for a smoke test")

    d = sub.add_parser("details", help="enrich listings with lat/lon and extras")
    d.add_argument("--source", default="imovirtual")
    d.add_argument("--limit", type=int, default=100)

    sub.add_parser("parse", help="drain raw_pages into listings and snapshots")

    r = sub.add_parser("daily", help="crawl, parse, enrich, prune, then publish the site and alert")
    r.add_argument("--max-pages", type=int)
    r.add_argument("--detail-limit", type=int, default=DETAIL_LIMIT)

    sub.add_parser("prune", help="delete parsed raw search pages past their retention")

    e = sub.add_parser("export", help="build the static site into a directory")
    e.add_argument("--out", required=True)

    pb = sub.add_parser("publish", help="deploy a built site to Cloudflare Pages")
    pb.add_argument("--dir", required=True)

    a = sub.add_parser("alert", help="send new and cheaper listings to Telegram")
    a.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")

    cl = sub.add_parser("commute-load", help="load a travel-time grid from scripts/commute_grid.py")
    cl.add_argument("csv")

    sub.add_parser("stats", help="what is in the warehouse right now")

    args = ap.parse_args(argv)
    _log(args.verbose)

    if args.cmd == "migrate":
        db.migrate()

    elif args.cmd == "crawl":
        if not (args.transaction and args.location):
            ap.error("crawl needs --transaction and --location")
        print(fetch.crawl_search(args.source, args.transaction,
                                 args.property_type, args.location, args.max_pages))

    elif args.cmd == "details":
        print(fetch.crawl_details(args.source, args.limit))

    elif args.cmd == "parse":
        print(parse.process_pending())

    elif args.cmd == "daily":
        return _daily(args)

    elif args.cmd == "prune":
        print(db.prune_raw_search(RAW_SEARCH_KEEP_DAYS))

    elif args.cmd == "export":
        print(export.build(args.out))

    elif args.cmd == "publish":
        export.publish(args.dir)

    elif args.cmd == "alert":
        print(alert.run(dry_run=args.dry_run))

    elif args.cmd == "commute-load":
        print(commute.load(args.csv))

    elif args.cmd == "stats":
        _stats()

    return 0


def _daily(args) -> int:
    """The nightly job. The crawl comes first and is committed page by page, so
    a failure in a later step never costs a day of history. Later steps run
    even when an earlier optional one fails, and the exit code says whether
    everything worked, so the Kubernetes Job shows red when it should."""
    failed = []

    def step(name, fn):
        try:
            result = fn()
            if result is not None:
                print(f"{name}: {result}")
        except Exception:  # noqa: BLE001 - recorded, and the job still exits non-zero
            logging.exception("step %s failed", name)
            failed.append(name)

    for transaction, ptype, location in DEFAULT_TARGETS:
        logging.info("crawling %s / %s / %s", transaction, ptype, location)
        step(f"crawl {transaction}/{ptype}",
             lambda t=transaction, p=ptype, loc=location:
                 fetch.crawl_search("imovirtual", t, p, loc, args.max_pages))
    step("parse", parse.process_pending)
    step("details", lambda: fetch.crawl_details("imovirtual", args.detail_limit))
    step("parse details", parse.process_pending)
    step("prune", lambda: db.prune_raw_search(RAW_SEARCH_KEEP_DAYS))

    if export.configured():
        site = export.site_dir()
        step("export", lambda: export.build(site))
        if "export" not in failed:
            step("publish", lambda: export.publish(site))
    else:
        logging.info("Cloudflare not configured, skipping the site")

    if alert.configured():
        step("alert", alert.run)
    else:
        logging.info("Telegram not configured, skipping the alert")

    if failed:
        logging.error("finished with failed steps: %s", ", ".join(failed))
        return 1
    return 0


def _stats() -> None:
    queries = [
        ("raw pages",        "SELECT count(*) n, count(*) FILTER (WHERE parsed_at IS NULL) pending FROM raw_pages"),
        ("listings",         "SELECT count(*) n, count(*) FILTER (WHERE latitude IS NOT NULL) geocoded FROM listings"),
        ("snapshots",        "SELECT count(*) n, min(observed_date) first_day, max(observed_date) last_day FROM listing_snapshots"),
        ("price changes",    "SELECT count(*) n FROM v_price_changes"),
    ]
    with db.connect() as conn:
        for label, sql in queries:
            row = conn.execute(sql).fetchone()
            print(f"{label:16} " + "  ".join(f"{k}={v}" for k, v in row.items()))

        print("\nmedian rent by parish (top 12 by volume):")
        rows = conn.execute(
            """SELECT parish, typology, n_listings, median_price, median_price_per_sqm
                 FROM v_parish_stats
                WHERE transaction_type = 'rent' AND council = 'Lisboa'
                ORDER BY n_listings DESC LIMIT 12"""
        ).fetchall()
        for r in rows:
            print(f"  {r['parish'][:28]:30} {str(r['typology'] or '-'):4} "
                  f"n={r['n_listings']:<5} median={r['median_price']}  "
                  f"eur/m2={r['median_price_per_sqm']}")


if __name__ == "__main__":
    sys.exit(main())
