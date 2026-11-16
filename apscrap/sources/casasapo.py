"""Casa Sapo (casa.sapo.pt).

Plain HTML search pages, which robots.txt allows. Each card has everything we
keep (type and typology, location, condition, area, price, advertiser, a long
description excerpt and a JSON-LD Offer with coordinates and the seller
type), so no detail page is read. 25 cards a page.

The portal rate-limits hard (429 after five pages in 76 s) and asks robots to
visit between 23:00 and 11:00 UTC, so pages are 45 s apart and the crawl
refuses to start outside that window. A search serves only about 1,360
results whatever it counts, so a council above that (Lisboa, 1,639) is
crawled parish by parish. Three councils are about 80 pages, an hour.
Removed adverts answer 410.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import math
import re

import httpx

from .. import geo
from .base import CrawlContext, Source
from .common import extras_from_text, money, number, per_sqm, property_type, text, typology

log = logging.getLogger(__name__)

BASE = "https://casa.sapo.pt"
PAGE_SIZE = 25
MAX_PAGES = 120
# Casa Sapo serves about 1,360 results per search however many it counts
# (Lisboa: 1,639 counted, page 55 half full, page 56 empty on 2026-11-16).
# A council counted above this is crawled parish by parish instead.
RESULT_CAP = 1300
VISIT_HOURS_UTC = (23, 11)     # robots.txt: Visit-time: 2300-1100
MIN_HOURS_BETWEEN = 12

_CARD = re.compile(r'<div id="property_([0-9a-f-]{36})" class="property(?: [^"]*)?">')
# Promoted cards link through an ad counter with the real address in l=.
_HREF = re.compile(r'href="(?:[^"]*?[?&;]l=)?(?:https://casa\.sapo\.pt)?(/alugar-[^"?]+\.html)')
_COUNT = re.compile(r"<h1>\s*([\d\s .&#xA0;]+)\s+Casas para alugar", re.I)
_LDJSON = re.compile(r'<script type="application/ld(?:&#x2B;|\+)json">\s*(\{.*?\})\s*</script>', re.S)


def _div(block: str, cls: str) -> str | None:
    m = re.search(r'<div class="' + re.escape(cls) + r'">(.*?)</div>', block, re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else None


def in_visit_window(now: dt.datetime | None = None) -> bool:
    h = (now or dt.datetime.now(dt.timezone.utc)).hour
    start, end = VISIT_HOURS_UTC
    return h >= start or h < end


def parse_cards(page: str) -> list[dict]:
    """Every card on a search page as a small dict, ready for raw_pages."""
    out = []
    starts = [(m.start(), m.group(1)) for m in _CARD.finditer(page)]
    for i, (pos, uid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(page)
        block = page[pos:end]
        href = _HREF.search(block)
        ld = None
        m = _LDJSON.search(block)
        if m:
            try:
                ld = json.loads(m.group(1))
            except ValueError:
                ld = None
        seller = (ld or {}).get("seller") or {}
        geo_ = ((ld or {}).get("availableAtOrFrom") or {}).get("geo") or {}
        photo = re.search(r'<img src="(https://media\.casasapo\.pt/[^"]+)"', block)
        agency = re.search(r'class="property-owner-logo">\s*<a [^>]*title="Veja todos os im[^"]*anunciante ([^"]+)"', block)
        out.append({
            "uid": uid,
            "url": BASE + href.group(1) if href else None,
            "type": _div(block, "property-type"),
            "location": _div(block, "property-location"),
            "features": _div(block, "property-features-text"),
            "price": _div(block, "property-price-value"),
            "price_type": _div(block, "property-price-type"),
            # The card holds the whole description; 1,500 characters keep what
            # the extras and the reading need without 7 MB of raw pages a night.
            "description": text(re.search(r'<div class="property-description">(.*?)</div>', block, re.S).group(1),
                                limit=1500)
                           if 'class="property-description"' in block else None,
            "seller_type": seller.get("@type"),
            "seller_name": seller.get("name") or (html.unescape(agency.group(1)) if agency else None),
            # 0,0 means "not given"
            "lat": geo_.get("latitude") or None, "lon": geo_.get("longitude") or None,
            "photo": photo.group(1) if photo else None,
        })
    return out


class CasaSapo(Source):
    name = "casasapo"
    delay = 45.0

    def crawl(self, ctx: CrawlContext, force: bool = False) -> None:
        if not force and not in_visit_window():
            # Not a failure: tonight's scheduled run is inside the window. With
            # no fresh observation, lifecycle.py leaves these adverts alone.
            log.info("casasapo: outside the 23:00-11:00 UTC visit window robots.txt asks for; skipping")
            return
        # Once a night is enough: a job started by hand earlier in the night
        # means the scheduled one leaves Casa Sapo alone.
        recent = ctx.conn.execute(
            """SELECT max(started_at) AS t FROM crawl_runs
                WHERE source = 'casasapo' AND status IN ('ok', 'incomplete') AND pages_fetched > 0
                  AND started_at > now() - make_interval(hours => %s)""", (MIN_HOURS_BETWEEN,)).fetchone()["t"]
        if recent and not force:
            log.info("casasapo: crawled at %s, less than %s h ago; skipping", recent, MIN_HOURS_BETWEEN)
            return
        retry_later = []          # (council, page, url) that failed on the first pass
        for council in ctx.councils:
            base = f"{BASE}/alugar-casas/{geo.slug(council)}/"
            first = self._walk(ctx, council, base, retry_later)
            if first and first["total"] and first["total"] > RESULT_CAP:
                parishes = self._parish_urls(first["html"], council)
                log.info("casasapo %s: %s counted, over the %s the site serves; by parish (%s)",
                         council, first["total"], RESULT_CAP, len(parishes))
                if not parishes:
                    ctx.problem(f"{council}: {first['total']} results, more than the site serves, "
                                "and no parish links to split by")
                    continue
                counted = 0
                for url in parishes:
                    got = self._walk(ctx, council, url, retry_later)
                    counted += (got or {}).get("total") or 0
                if counted < first["total"] * 0.95:
                    ctx.problem(f"{council}: parishes add up to {counted} of {first['total']}")

        # Second pass: the gateway's 504s come in bursts, and a page that
        # failed three times in a row usually answers a few minutes later.
        for council, n, url in retry_later:
            status, body, cards = self._page(ctx, url)
            if status == 200 and cards:
                self._keep(ctx, url, cards, council, n, None)
                log.info("casasapo %s page %s: read on the second pass", council, n)
            else:
                ctx.save("search", url, status, None)
                ctx.page_failed(f"{url} HTTP {status}")

    def _walk(self, ctx: CrawlContext, council: str, base: str, retry_later: list) -> dict | None:
        """Every page of one search. Returns its first page (count and HTML),
        or None when page 1 failed. A council over RESULT_CAP is walked only up
        to page 1 here and then again by parish."""
        n, total_pages, failed_in_a_row, first = 1, 1, 0, None
        while n <= min(total_pages, MAX_PAGES):
            url = base if n == 1 else f"{base}?pn={n}"
            status, body, cards = self._page(ctx, url)
            if status != 200 or (not cards and n == 1 and not _COUNT.search(body)):
                if n == 1 or failed_in_a_row >= 2:
                    # Without page 1 there is no page count, and three in a
                    # row is an outage, not a blip: this search is missing.
                    ctx.save("search", url, status, None)
                    ctx.problem(f"{council}: {url} HTTP {status}; stopped this search at page {n}")
                    return first
                failed_in_a_row += 1
                retry_later.append((council, n, url))
                n += 1
                continue
            failed_in_a_row = 0
            total = None
            if n == 1:
                m = _COUNT.search(body)
                if m:
                    total = int(re.sub(r"\D", "", html.unescape(m.group(1))) or 0)
                    total_pages = max(1, math.ceil(total / PAGE_SIZE))
                else:
                    # No count (the page changed): page until one comes back
                    # empty, rather than stop at page 1 and have lifecycle.py
                    # read everything after it as gone.
                    log.warning("casasapo: no result count on %s", url)
                    total_pages = MAX_PAGES
                first = {"total": total, "html": body}
                if total and total > RESULT_CAP and base.rstrip("/").count("/") == 4:
                    # A whole council over the cap: keep page 1, the parishes
                    # cover the rest.
                    self._keep(ctx, url, cards, council, n, total)
                    return first
            self._keep(ctx, url, cards, council, n, total)
            log.info("casasapo %s page %s/%s (%s)", council, n, total_pages, url.split(".pt")[-1])
            if not cards:
                break
            n += 1
        return first

    @staticmethod
    def _parish_urls(page: str, council: str) -> list[str]:
        """Search URLs of the council's parishes, from the links on its first
        page that name a CAOP parish ("/alugar-casas/lisboa/avenidas-novas/")."""
        c = geo.slug(council)
        names = {geo.slug(p["parish"]) for p in geo._parishes() if p["council"] == council}
        slugs = sorted({s for s in re.findall(r'href="/alugar-(?:casas|apartamentos|moradias)/' + re.escape(c)
                                              + r'/([a-z0-9-]+)/"', page) if s in names})
        return [f"{BASE}/alugar-casas/{c}/{s}/" for s in slugs]

    @staticmethod
    def _page(ctx: CrawlContext, url: str) -> tuple[int, str, list[dict]]:
        try:
            resp = ctx.fetcher.get(url)
            status, body = resp.status_code, resp.text
        except httpx.HTTPStatusError as exc:     # still failing after the retries
            status, body = exc.response.status_code, ""
        return status, body, (parse_cards(body) if status == 200 else [])

    @staticmethod
    def _keep(ctx: CrawlContext, url: str, cards: list[dict], council: str, n: int, total) -> None:
        ctx.save("search", url, 200, {"items": cards, "council": council, "page": n, "total": total})
        ctx.items += len(cards)
        ctx.pages_ok += 1

    # -- parsing ----------------------------------------------------------------

    def parse_page(self, kind: str, payload: dict):
        if kind != "search":
            return
        for c in payload.get("items", []):
            rec = self._card(c, payload.get("council"))
            if rec:
                yield rec

    def _card(self, c: dict, crawl_council: str | None):
        if not c.get("uid") or not c.get("url") or (c.get("price_type") or "alugar") != "alugar":
            return None
        ptype = property_type(c.get("type"))
        if not ptype:
            return None
        # "Pontinha, Pontinha e Famões, Odivelas, Distrito de Lisboa":
        # neighbourhood, parish, council, district (fewer parts in the city).
        parts = [p.strip() for p in (c.get("location") or "").split(",") if p.strip()]
        parts = [p for p in parts if not p.lower().startswith("distrito")]
        council_txt = parts[-1] if parts else crawl_council
        parish_txt = parts[-2] if len(parts) >= 2 else None
        neighborhood = parts[0] if len(parts) >= 3 else None
        lat, lon = c.get("lat"), c.get("lon")
        council, parish = geo.place(council_txt, parish_txt, lat, lon,
                                    extra_texts=(neighborhood,) if neighborhood else ())
        feats = (c.get("features") or "").split("·")
        area = next((number(x) for x in feats if "m²" in x or "m2" in x), None)
        condition = next((x.strip() for x in feats if x.strip() and "m²" not in x and "m2" not in x), None)
        price = money(c.get("price"))
        private = c.get("seller_type") == "Person"
        title = " ".join(x for x in (c.get("type"), parish or parish_txt, council) if x)
        desc = c.get("description")
        listing = {
            "source": self.name, "external_id": c["uid"], "url": c["url"],
            "transaction_type": "rent", "property_type": ptype,
            "typology": typology(c.get("type")),
            "area_sqm": area, "council": council, "parish": parish,
            "district": "Lisboa", "neighborhood": neighborhood,
            "is_private_owner": private,
            "agency_name": None if private else c.get("seller_name"),
            "thumbnail_url": c.get("photo"),
            "latitude": lat, "longitude": lon,
            "condition": condition, "description": desc,
            "extras": extras_from_text(desc) or None,
        }
        snapshot = {"price_eur": price, "price_per_sqm": per_sqm(price, area), "title": title} if price else None
        return listing, snapshot
