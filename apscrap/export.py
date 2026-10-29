"""Stage 3: turn the warehouse into a static site and publish it.

The site is site/ (hand-written HTML, CSS and JS) plus one data.json written
here every night. Nothing runs on a server when you open the page: Cloudflare
Pages serves two files and the browser does the filtering. That is the whole
reason there is no Metabase in production, which needed 2 GB the node does not
have.
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .config import SITE_SRC
from .db import connect

log = logging.getLogger(__name__)

PAGES_PROJECT = os.getenv("APSCRAP_PAGES_PROJECT", "apscrap")

# Only what site/app.js reads. Every extra column costs ~5% of the file.
LISTINGS = """
SELECT id, url, title, thumbnail_url, property_type, typology,
       area_sqm, price_eur, council, parish,
       round(geo_lat::numeric, 5) AS geo_lat, round(geo_lon::numeric, 5) AS geo_lon,
       geo_precision, commute_min, days_on_market,
       first_seen_at::date AS first_seen, listed_at::date AS listed,
       n_drops, max_price, last_drop_on, price_path,
       deal_score, peer_level, peer_n, peer_med_price,
       is_temporary, is_private_owner
FROM v_deals
"""

# The site rebuilds these prefixes (app.js, listingUrl / thumbUrl). Together
# they were a quarter of data.json.
URL_PREFIX = "https://www.imovirtual.com/pt/anuncio/"
THUMB_PREFIX = "https://ireland.apollo.olxcdn.com/v1/files/"
THUMB_SUFFIX = "/image;s=655x491;q=80"

# Median rent per m² per council per day, every typology together. Flats only:
# a house and a flat per m² are different markets.
MARKET_DAILY = """
SELECT s.observed_date AS day, l.council, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY s.price_per_sqm)::numeric, 2) AS ppm,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY s.price_eur)::numeric, 0)     AS eur
FROM listing_snapshots s
JOIN listings l ON l.id = s.listing_id
WHERE l.transaction_type = 'rent' AND l.property_type = 'flat'
  AND s.price_eur BETWEEN 200 AND 20000 AND s.price_per_sqm BETWEEN 2 AND 100
  AND l.area_sqm BETWEEN 10 AND 3000
GROUP BY 1, 2
HAVING count(*) >= 10
ORDER BY 1, 2
"""

PARISHES = """
SELECT council, parish, property_type, typology, n_listings, median_price,
       p25_price, p75_price, median_price_per_sqm, median_area_sqm, avg_days_on_market
FROM v_parish_stats
WHERE transaction_type = 'rent' AND n_listings >= 3
"""

TOTALS = """
SELECT
  (SELECT count(*) FROM listings WHERE transaction_type = 'rent'
      AND last_seen_at > now() - interval '3 days')                       AS active_rent,
  (SELECT count(*) FROM listings WHERE transaction_type = 'sale'
      AND last_seen_at > now() - interval '3 days')                       AS active_sale,
  (SELECT min(observed_date) FROM listing_snapshots)                      AS history_from,
  (SELECT count(DISTINCT observed_date) FROM listing_snapshots)           AS history_days,
  (SELECT max(finished_at) FROM crawl_runs WHERE status = 'ok')           AS last_crawl,
  (SELECT count(*) FROM listings WHERE latitude IS NOT NULL
      AND transaction_type = 'rent')                                      AS geocoded_rent
"""


def configured() -> bool:
    return bool(os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("CLOUDFLARE_ACCOUNT_ID"))


def site_dir() -> str:
    return os.getenv("APSCRAP_SITE_DIR", "/tmp/apscrap-site")


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def _dedupe(rows: list[dict]) -> list[dict]:
    """The same flat posted twice, usually by the same agency (2.7% of adverts,
    LIMITATIONS.md). Keep the one we saw first and count the copies, so supply
    reads right and the deal list does not show one flat twice."""
    seen: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: (r["first_seen"], r["id"])):
        key = (r["council"], r["parish"], r["typology"], r["area_sqm"], r["price_eur"],
               (r["title"] or "").strip().lower())
        if key in seen:
            seen[key]["dupes"] = seen[key].get("dupes", 1) + 1
        else:
            seen[key] = r
    return list(seen.values())


def _compact(r: dict) -> dict:
    """Drop empty values and the shared URL prefixes. The page treats a
    missing key exactly like null, false or zero."""
    url, thumb = r["url"], r["thumbnail_url"]
    if url and url.startswith(URL_PREFIX):
        r["url"] = url[len(URL_PREFIX):]
    if thumb and thumb.startswith(THUMB_PREFIX) and thumb.endswith(THUMB_SUFFIX):
        r["thumbnail_url"] = thumb[len(THUMB_PREFIX):-len(THUMB_SUFFIX)]
    for k in ("price_eur", "max_price", "peer_med_price", "area_sqm"):
        if r.get(k) is not None:
            r[k] = int(round(r[k]))
    return {k: v for k, v in r.items() if v not in (None, False, 0, "")}


def build(out: str) -> dict:
    out_dir = Path(out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(SITE_SRC, out_dir)

    with connect() as conn:
        listings = _dedupe(conn.execute(LISTINGS).fetchall())
        market = conn.execute(MARKET_DAILY).fetchall()
        parishes = conn.execute(PARISHES).fetchall()
        totals = conn.execute(TOTALS).fetchone()
        commute = conn.execute(
            "SELECT label, departure FROM commute_meta WHERE id = 1"
        ).fetchone()

    data = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "totals": totals,
        "commute": commute,
        "listings": [_compact(r) for r in listings],
        "market_daily": market,
        "parishes": parishes,
    }
    path = out_dir / "data.json"
    path.write_text(json.dumps(data, default=_plain, ensure_ascii=False, separators=(",", ":")))
    return {"dir": str(out_dir), "listings": len(listings),
            "data_kib": round(path.stat().st_size / 1024)}


def publish(directory: str) -> None:
    """wrangler reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from the
    environment. The token needs only Account > Cloudflare Pages > Edit."""
    cmd = ["wrangler", "pages", "deploy", directory,
           "--project-name", PAGES_PROJECT, "--branch", "main", "--commit-dirty=true"]
    log.info("publishing %s to Pages project %s", directory, PAGES_PROJECT)
    subprocess.run(cmd, check=True)
