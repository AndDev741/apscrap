"""RE/MAX Portugal (remax.pt).

The search API is under /api, which robots.txt disallows, so the crawl reads
what is allowed: the listing sitemaps (every advert online, with a lastmod),
then the detail page of each advert that is new, changed since we last read
it, or not read for a week. The detail page carries the whole advert as
base64 JSON in __NEXT_DATA__ (props.pageProps.listingEncoded): exact
coordinates, publish date, previous price.

An advert that leaves the sitemap is gone; its page answers 404. Many let
flats stay in the sitemap for a while with isSold (listingStatusID 6), and
reserved ones carry status 2, so RE/MAX does say "let" and "reserved".
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import json
import logging
import re

from .. import geo
from .base import CrawlContext, Source
from .common import per_sqm, property_type, text, typology, ts

log = logging.getLogger(__name__)

BASE = "https://remax.pt"
IMG = "https://i.maxwork.pt/ds-l/"
_SITEMAP = re.compile(r"https://remax\.pt/sitemap/listings_details_pt_\d+\.xml\.gz")
_URL = re.compile(r"<loc>(https://remax\.pt/pt/imoveis/([^/<]+)/([\d-]+))</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?")
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

ENERGY = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F", 7: "G", 8: "A+", 12: "B-",
          21: "G", 22: "G"}
# listingAttributesIds onto the app's extras (from the site's own i18n table).
ATTRS = {2: "Elevador", 4: "Garagem", 5: "Varanda", 8: "Varanda", 9: "Jardim", 7: "Piscina",
         10: "Mobilado", 11: "Arrecadação", 51: "Ar condicionado"}
HOME_WORDS = ("apartamento", "moradia", "duplex", "triplex", "estudio", "andar-moradia",
              "penthouse", "loft")
REFRESH_DAYS = 7
MAX_PHOTOS = 12


def _slug_parts(slug: str, councils: list[str]):
    """'arrendamento-apartamento-t2-odivelas-odivelas' ->
    ('apartamento', 'T2', 'Odivelas', 'odivelas'), or None when it is not a
    home for rent in one of the councils."""
    if not slug.startswith("arrendamento-"):
        return None
    rest = slug[len("arrendamento-"):]
    kind = next((w for w in HOME_WORDS if rest.startswith(w + "-")), None)
    if not kind:
        return None
    rest = rest[len(kind) + 1:]
    m = re.match(r"t(\d+)-(.*)", rest)
    typ, rest = (f"T{min(int(m.group(1)), 6)}", m.group(2)) if m else (None, rest)
    for c in councils:
        s = geo.slug(c)
        if rest == s or rest.startswith(s + "-"):
            return kind, typ, c, rest[len(s) + 1:]
    return None


class Remax(Source):
    name = "remax"

    def crawl(self, ctx: CrawlContext) -> None:
        f = ctx.fetcher
        index = f.get(BASE + "/sitemap.xml")
        maps = sorted(set(_SITEMAP.findall(index.text))) if index.status_code == 200 else []
        if not maps:
            ctx.problem(f"no listing sitemaps in sitemap.xml (HTTP {index.status_code})")
            return
        items = []
        for url in maps:
            resp = f.get(url)
            if resp.status_code != 200:
                ctx.problem(f"{url}: HTTP {resp.status_code}")
                return    # an incomplete list would mark live adverts gone
            body = resp.content
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            for loc, slug, ext_id, lastmod in _URL.findall(body.decode("utf-8", "replace")):
                parts = _slug_parts(slug, ctx.councils)
                if parts:
                    items.append({"id": ext_id, "url": loc, "slug": slug, "lastmod": lastmod or None})
        ctx.save("search", BASE + "/sitemap/listings_details_pt", 200, {"items": items})
        ctx.items += len(items)
        log.info("remax: %s rentals in %s", len(items), ", ".join(ctx.councils))

        # Which detail pages to read: new, changed since read, or stale.
        known = {r["external_id"]: r["detail_fetched_at"] for r in ctx.conn.execute(
            "SELECT external_id, detail_fetched_at FROM listings WHERE source = 'remax'").fetchall()}
        now = dt.datetime.now(dt.timezone.utc)
        new, changed, stale = [], [], []
        for it in items:
            fetched = known.get(it["id"])
            lastmod = ts(it["lastmod"])
            if fetched is None:
                new.append(it)
            elif lastmod and lastmod > fetched:
                changed.append(it)
            elif fetched < now - dt.timedelta(days=REFRESH_DAYS):
                stale.append((fetched, it))
        queue = new + changed + [it for _, it in sorted(stale, key=lambda x: x[0])]
        log.info("remax: %s new, %s changed, %s stale; reading %s",
                 len(new), len(changed), len(stale), min(len(queue), ctx.detail_limit))
        for it in queue[:ctx.detail_limit]:
            resp = f.get(it["url"])
            payload = self.listing_payload(resp.text) if resp.status_code == 200 else None
            ctx.save("listing", it["url"], resp.status_code, payload)
            if resp.status_code in (404, 410):
                from ..lifecycle import mark_removed
                row = ctx.conn.execute("SELECT id FROM listings WHERE source = 'remax' AND external_id = %s",
                                       (it["id"],)).fetchone()
                if row:
                    mark_removed(ctx.conn, row["id"])
                    ctx.conn.commit()

    @staticmethod
    def listing_payload(html: str) -> dict | None:
        m = _NEXT.search(html)
        if not m:
            return None
        try:
            pp = json.loads(m.group(1))["props"]["pageProps"]
            ad = json.loads(base64.b64decode(pp["listingEncoded"]).decode("utf-8"))
        except (KeyError, ValueError, TypeError):
            return None
        keep = ("id", "listingTitle", "listingPrice", "previousPrice", "lastPriceReductionDate",
                "totalArea", "livingArea", "numberOfBedrooms", "listingTypeID", "businessTypeID",
                "regionName1", "regionName2", "regionName3", "localZone", "address", "zipCode",
                "latitude", "longitude", "isExactAddress", "publishDate", "modified",
                "energyEfficiencyLevelID", "constructionYear", "floorAsNumber", "officeName",
                "listingStatusID", "isSold", "isActive", "isOnline", "elevator", "garage",
                "parking", "garageSpots", "listingAttributesIds", "descriptionTags", "marketDays")
        out = {k: ad.get(k) for k in keep}
        out["photos"] = [p if isinstance(p, str) else (p or {}).get("url")
                         for p in (ad.get("listingPictures") or [])][:MAX_PHOTOS]
        desc = next((d.get("description") for d in ad.get("descriptions") or []
                     if d.get("languageCode") == "PT"), None)
        out["description"] = text(desc)
        return out

    # -- parsing ----------------------------------------------------------------

    def parse_page(self, kind: str, payload: dict):
        if kind == "search":
            for it in payload.get("items", []):
                parts = _slug_parts(it["slug"], geo.COUNCILS)
                kind_word, typ, council = (parts or (None, None, None, None))[:3]
                listing = {
                    "source": self.name, "external_id": it["id"], "url": it["url"],
                    "transaction_type": "rent", "property_type": property_type(kind_word),
                    "typology": typ, "council": council,
                    "source_updated_at": ts(it.get("lastmod")),
                }
                yield listing, None
        elif kind == "listing":
            rec = self._listing(payload)
            if rec:
                yield rec

    def _listing(self, ad: dict):
        ext_id = ad.get("listingTitle")
        if not ext_id or ad.get("businessTypeID") not in (2, None):
            return None
        slug = ad.get("descriptionTags") or ""
        ptype = property_type(slug.replace("arrendamento-", ""))
        lat, lon = ad.get("latitude"), ad.get("longitude")
        council, parish = geo.place(ad.get("regionName2"), ad.get("regionName3"), lat, lon,
                                    extra_texts=(ad.get("localZone"),))
        area = ad.get("livingArea") or ad.get("totalArea")
        area = float(area) if area and area > 0 else None
        price = ad.get("listingPrice")
        price = float(price) if price and price > 0 else None
        attrs = set(ad.get("listingAttributesIds") or [])
        extras = sorted({ATTRS[a] for a in attrs if a in ATTRS}
                        | ({"Elevador"} if ad.get("elevator") else set())
                        | ({"Garagem"} if ad.get("garage") or ad.get("parking") else set()))
        photos = [IMG + p for p in ad.get("photos") or [] if p]
        title_bits = [ptype == "house" and "Moradia" or "Apartamento",
                      typology(slug, bedrooms=ad.get("numberOfBedrooms")),
                      ad.get("regionName3") or ad.get("regionName2")]
        # listingStatusID: 1 available, 2 reserved, 6 let or sold (with isSold).
        # Anything else is left as it was.
        status_id = ad.get("listingStatusID")
        rented = bool(ad.get("isSold")) or status_id == 6
        status = "rented" if rented else {1: "available", 2: "reserved"}.get(status_id)
        listing = {
            "source": self.name, "external_id": ext_id,
            "url": f"{BASE}/pt/imoveis/{slug}/{ext_id}" if slug else f"{BASE}/pt/{ext_id}",
            "transaction_type": "rent", "property_type": ptype,
            "typology": typology(slug, bedrooms=ad.get("numberOfBedrooms")),
            "area_sqm": area,
            "floor": str(ad["floorAsNumber"]) if ad.get("floorAsNumber") is not None else None,
            "district": ad.get("regionName1"), "council": council, "parish": parish,
            "neighborhood": ad.get("localZone"), "street": ad.get("address"),
            "is_private_owner": False, "agency_name": ad.get("officeName"),
            "listed_at": ts(ad.get("publishDate")),
            "thumbnail_url": photos[0] if photos else None,
            "latitude": lat, "longitude": lon,
            "energy_cert": ENERGY.get(ad.get("energyEfficiencyLevelID")),
            "build_year": ad.get("constructionYear") or None,
            "description": ad.get("description"),
            "extras": extras or None, "image_urls": photos or None,
            "source_updated_at": ts(ad.get("modified")),
            "status": status,
            "_detail": True,
        }
        snapshot = None
        if price and not rented:
            snapshot = {"price_eur": price, "price_per_sqm": per_sqm(price, area),
                        "title": " ".join(b for b in title_bits if b)}
        return listing, snapshot
