"""KW Portugal (kwportugal.pt).

robots.txt allows everything for ordinary user agents. The search runs on a
GraphQL endpoint we have not located, so the crawl reads the property
sitemaps (every advert, including let ones) and the detail page of each rental
in the councils being followed. The detail page is a Next.js server render
with the property object in its flight data, plus isRented / isReserved /
isSold flags: KW keeps let flats online, so like ERA it tells us when a flat
was let, not just that it vanished.

The sitemap's lastmod is the time of the request, so it says nothing about
changes. Adverts that are not let are re-read every night (a few dozen in
the three default councils); let ones are left alone.
"""
from __future__ import annotations

import json
import logging
import re

from .. import geo
from .base import CrawlContext, Source
from .common import money, per_sqm, property_type, text, typology

log = logging.getLogger(__name__)

BASE = "https://www.kwportugal.pt"
_MAPS = re.compile(r"<loc>(https://www\.kwportugal\.pt/pt/imoveis/sitemap/\d+\.xml)</loc>")
_LOC = re.compile(r"<loc>(https://www\.kwportugal\.pt/pt/Imovel/([^<]+))</loc>")
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
_FLAGS = re.compile(r'"isSold":(true|false),"isReserved":(true|false),"isRented":(true|false)')
HOME_TYPES = ("apartamento", "moradia", "duplex", "triplex", "estudio", "loft", "penthouse")
MAX_PHOTOS = 12


def _path_parts(path: str, councils: list[str]):
    """'Arrendamento/Apartamento/Lisboa/Odivelas/Odivelas/3454/KWPT-022439'
    -> dict, or None when it is not a home for rent in one of the councils."""
    bits = path.split("/")
    if len(bits) < 7 or bits[0].lower() != "arrendamento":
        return None
    kind = bits[1].lower()
    if not any(kind.startswith(w) for w in HOME_TYPES):
        return None
    council = next((c for c in councils if geo.slug(c) == geo.slug(bits[3].replace("_", " "))), None)
    if not council:
        return None
    return {"kind": kind, "council": council, "parish": bits[4].replace("-", " ").replace("_", " "),
            "id": bits[-2], "ref": bits[-1]}


def _undef(v):
    return None if v in ("$undefined", "", None) else v


class Kw(Source):
    name = "kw"

    def crawl(self, ctx: CrawlContext) -> None:
        f = ctx.fetcher
        index = f.get(BASE + "/sitemap-pt.xml")
        maps = _MAPS.findall(index.text) if index.status_code == 200 else []
        if not maps:
            ctx.problem(f"no property sitemaps in sitemap-pt.xml (HTTP {index.status_code})")
            return
        items = []
        for url in maps:
            resp = f.get(url)
            if resp.status_code != 200:
                ctx.problem(f"{url}: HTTP {resp.status_code}")
                return
            for loc, path in _LOC.findall(resp.text):
                parts = _path_parts(path, ctx.councils)
                if parts:
                    items.append({"url": loc, **parts})
        ctx.save("search", BASE + "/pt/imoveis/sitemap", 200, {"items": items})
        ctx.items += len(items)

        known = {r["external_id"]: r for r in ctx.conn.execute(
            """SELECT external_id, detail_fetched_at, gone_reason FROM listings
                WHERE source = 'kw'""").fetchall()}
        new = [it for it in items if it["ref"] not in known
               or known[it["ref"]]["detail_fetched_at"] is None]
        live = sorted((it for it in items if it["ref"] in known
                       and known[it["ref"]]["detail_fetched_at"] is not None
                       and known[it["ref"]]["gone_reason"] != "rented"),
                      key=lambda it: known[it["ref"]]["detail_fetched_at"])
        queue = (new + live)[:ctx.detail_limit]
        log.info("kw: %s rentals in the sitemap, %s new, %s live to refresh; reading %s",
                 len(items), len(new), len(live), len(queue))
        for it in queue:
            resp = f.get(it["url"])
            payload = self.listing_payload(resp.text, it["id"]) if resp.status_code == 200 else None
            if payload is None and resp.status_code == 200:
                status = 404 if "NEXT_HTTP_ERROR_FALLBACK;404" in resp.text else 200
            else:
                status = resp.status_code
            ctx.save("listing", it["url"], status, payload)
            if payload is None:
                # Not a page we can read (soft 404 included): note it so the
                # same advert is not retried every night.
                ctx.conn.execute(
                    "UPDATE listings SET detail_fetched_at = now() WHERE source = 'kw' AND external_id = %s",
                    (it["ref"],))
                if status == 404:
                    from ..lifecycle import mark_removed
                    row = ctx.conn.execute("SELECT id FROM listings WHERE source = 'kw' AND external_id = %s",
                                           (it["ref"],)).fetchone()
                    if row:
                        mark_removed(ctx.conn, row["id"])
                ctx.conn.commit()

    @staticmethod
    def listing_payload(page: str, prop_id: str) -> dict | None:
        chunks = _FLIGHT.findall(page)
        if not chunks:
            return None
        try:
            stream = json.loads('"' + "".join(chunks) + '"')
        except ValueError:
            return None
        prop = None
        for m in re.finditer(r'\{"idProperty":' + re.escape(str(prop_id)) + r',', stream):
            try:
                obj, _ = json.JSONDecoder().raw_decode(stream, m.start())
            except ValueError:
                continue
            if prop is None or len(obj) > len(prop):
                prop = obj
        if not prop:
            return None
        keep = ("idProperty", "reference", "idBusiness", "business", "type", "subtype", "state",
                "region1", "region2", "region3", "latitude", "longitude", "poBox", "locality",
                "rooms", "bathrooms", "parkingPlaces", "livingArea", "totalArea", "constructionYear",
                "energyClass", "name", "price", "previousPrice", "priceChangeDate", "rentPrice",
                "previousRentPrice", "typology", "idSituation", "floor", "agencyName",
                "hasBalcony", "hasTerrace", "hasParking", "hasLift", "hasPool", "depositValue",
                "depositNumberRents", "createdAt", "created_at")
        out = {k: _undef(prop.get(k)) for k in keep}
        desc = _undef(prop.get("description"))
        if isinstance(desc, str) and desc.startswith("$"):
            # a reference to another chunk of the stream ("$4e"): look it up
            ref = re.search(r'(?:^|\n)' + re.escape(desc[1:]) + r':T[0-9a-f]+,', stream)
            desc = stream[ref.end():ref.end() + 6000].split("\n")[0] if ref else None
        out["description"] = text(desc)
        out["photos"] = [i.get("url") for i in (prop.get("images") or []) if isinstance(i, dict)][:MAX_PHOTOS]
        flags = _FLAGS.search(stream)
        out["flags"] = ({"sold": flags.group(1) == "true", "reserved": flags.group(2) == "true",
                         "rented": flags.group(3) == "true"} if flags else None)
        return out

    # -- parsing ----------------------------------------------------------------

    def parse_page(self, kind: str, payload: dict):
        if kind == "search":
            for it in payload.get("items", []):
                listing = {"source": self.name, "external_id": it["ref"], "url": it["url"],
                           "transaction_type": "rent", "property_type": property_type(it["kind"]),
                           "council": it["council"]}
                yield listing, None
        elif kind == "listing":
            rec = self._listing(payload)
            if rec:
                yield rec

    def _listing(self, p: dict):
        ref = p.get("reference")
        if not ref:
            return None
        flags = p.get("flags") or {}
        rented = bool(flags.get("rented") or flags.get("sold")) or p.get("idSituation") in (4, 6)
        reserved = bool(flags.get("reserved")) or p.get("idSituation") == 5
        lat, lon = p.get("latitude"), p.get("longitude")
        council, parish = geo.place(p.get("region2"), p.get("region3"), lat, lon,
                                    extra_texts=(p.get("locality"),))
        area = p.get("livingArea") or p.get("totalArea")
        area = float(area) if area and area > 0 else None
        price = money(p.get("rentPrice")) or money(p.get("price"))
        extras = [name for key, name in (("hasLift", "Elevador"), ("hasParking", "Garagem"),
                                         ("hasBalcony", "Varanda"), ("hasTerrace", "Varanda"),
                                         ("hasPool", "Piscina")) if p.get(key)]
        photos = [u for u in p.get("photos") or [] if u]
        title = p.get("name") or " ".join(x for x in (p.get("type"), p.get("typology"), parish) if x)
        listing = {
            "source": self.name, "external_id": ref,
            "transaction_type": "rent", "property_type": property_type(p.get("type")),
            "typology": typology(p.get("typology"), bedrooms=p.get("rooms")),
            "area_sqm": area, "district": p.get("region1"), "council": council, "parish": parish,
            "neighborhood": (p.get("locality") or "").title() or None,
            "is_private_owner": False, "agency_name": p.get("agencyName") or "KW",
            "listed_at": None, "thumbnail_url": photos[0] if photos else None,
            "latitude": lat, "longitude": lon,
            "energy_cert": p.get("energyClass"), "build_year": p.get("constructionYear") or None,
            "condition": p.get("state"), "description": p.get("description"),
            "deposit_eur": money(p.get("depositValue")),
            "extras": sorted(set(extras)) or None, "image_urls": photos or None,
            "status": "rented" if rented else ("reserved" if reserved else "available"),
            "_detail": True,
        }
        snapshot = None
        if price and not rented:
            snapshot = {"price_eur": price, "price_per_sqm": per_sqm(price, area), "title": title}
        return listing, snapshot
