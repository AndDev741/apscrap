"""ERA Portugal (era.pt).

The search page calls a JSON API that robots.txt allows
(POST /API/ServicesModule/Property/Search). It wants the anti-forgery token
from the /arrendar page and that page's cookies, sent the way the page sends
them. 15 adverts a page; the three default councils fit in about ten pages.

ERA keeps let flats online as "Já ERA" and marks reserved ones "Reservado",
which no other portal we read does: the only direct "rented" signal we get.
Detail (description, parish) comes from a second allowed endpoint, fetched
once per advert.
"""
from __future__ import annotations

import logging
import re

from .. import geo
from .base import CrawlContext, Source
from .common import extras_from_text, money, number, per_sqm, property_type, text, typology

log = logging.getLogger(__name__)

BASE = "https://www.era.pt"
SEARCH = BASE + "/API/ServicesModule/Property/Search"
DETAIL = BASE + "/API/ServicesModule/Property/PropertyDetailByReference"
_TOKEN = re.compile(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"')

# INE council codes, as ERA's filter wants them ("11-06").
INE = {"Alenquer": "1101", "Arruda dos Vinhos": "1102", "Azambuja": "1103", "Cadaval": "1104",
       "Cascais": "1105", "Lisboa": "1106", "Loures": "1107", "Lourinhã": "1108", "Mafra": "1109",
       "Oeiras": "1110", "Sintra": "1111", "Sobral de Monte Agraço": "1112",
       "Torres Vedras": "1113", "Vila Franca de Xira": "1114", "Amadora": "1115", "Odivelas": "1116"}
PROPERTY_TYPES = {1: "flat", 2: "house"}   # Apartamentos, Moradias
STATUS = {"disponível": "available", "disponivel": "available", "reservado": "reserved",
          "já era": "rented", "ja era": "rented", "arrendado": "rented", "vendido": "rented"}
MAX_PAGES = 60


def _coord(v) -> float | None:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


class Era(Source):
    name = "era"

    def crawl(self, ctx: CrawlContext) -> None:
        f = ctx.fetcher
        page = f.get(BASE + "/arrendar")
        m = _TOKEN.search(page.text)
        if page.status_code != 200 or not m:
            ctx.problem(f"no anti-forgery token on /arrendar (HTTP {page.status_code})")
            return
        headers = {"RequestVerificationToken": m.group(1), "ModuleId": "411", "TabId": "40",
                   "Accept": "application/json, text/plain, */*", "Referer": BASE + "/arrendar",
                   "X-Requested-With": "XMLHttpRequest"}
        counties = [f"11-{INE[c][2:]}" for c in ctx.councils if c in INE]
        if not counties:
            ctx.problem(f"no ERA council codes for {ctx.councils}")
            return
        seen = []
        for type_id in PROPERTY_TYPES:
            n = 1
            total_pages = 1
            while n <= min(total_pages, MAX_PAGES):
                body = {"page": n, "businessTypeId": [2], "propertiesTypeId": [type_id],
                        "isResidential": True, "districts": ["11"], "counties": counties, "order": 3}
                resp = f.post(SEARCH, json=body, headers=headers)
                data = resp.json() if resp.status_code == 200 else None
                items = (data or {}).get("PropertyList")
                if items is None:
                    ctx.save("search", f"{SEARCH}#type={type_id}&page={n}", resp.status_code, None)
                    ctx.problem(f"search page {n} for type {type_id}: HTTP {resp.status_code}")
                    break
                slim = []
                for it in items:
                    gallery = it.get("Gallery") or []
                    it = {k: v for k, v in it.items() if k != "Gallery"}
                    it["_photos"] = [g.get("Url") for g in gallery[:12] if isinstance(g, dict)]
                    it["_property_type"] = PROPERTY_TYPES[type_id]
                    slim.append(it)
                ctx.save("search", f"{SEARCH}#type={type_id}&page={n}", 200,
                         {"items": slim, "page": n, "total": data.get("TotalRecords")})
                ctx.items += len(slim)
                seen += slim
                total_pages = int(data.get("TotalPages") or 1)
                log.info("era type %s page %s/%s", type_id, n, total_pages)
                n += 1

        # Descriptions and parish, once per advert, for the ones still let
        # out (a "Já ERA" flat needs no description).
        done = {r["external_id"] for r in ctx.conn.execute(
            "SELECT external_id FROM listings WHERE source = 'era' AND detail_fetched_at IS NOT NULL")}
        refs = [it["Reference"] for it in seen
                if it.get("Reference") and it["Reference"] not in done
                and STATUS.get(((it.get("BusinessStatus") or {}).get("Name") or "").strip().lower()) != "rented"]
        dheaders = {**headers, "ModuleId": "641", "TabId": "256"}
        for ref in refs[:ctx.detail_limit]:
            url = f"{DETAIL}?reference={ref}"
            resp = f.get(url, headers=dheaders)
            data = resp.json() if resp.status_code == 200 and resp.content[:1] == b"{" else None
            if data:
                data = {k: v for k, v in data.items()
                        if k in ("Reference", "Description", "LocalizationDetail", "OtherFeatures",
                                 "Floor", "Elevator", "Parking", "Ce", "TitleDetail")}
            ctx.save("detail", url, resp.status_code, data)

    # -- parsing ----------------------------------------------------------------

    def parse_page(self, kind: str, payload: dict):
        if kind == "search":
            for it in payload.get("items", []):
                rec = self._item(it)
                if rec:
                    yield rec
        elif kind == "detail":
            rec = self._detail(payload)
            if rec:
                yield rec

    def _item(self, it: dict):
        ref = str(it.get("Reference") or "").strip()
        url = it.get("DetailUrl")
        if not ref or not url:
            return None
        title = (it.get("Title") or "").replace(" / ", " ").strip()
        ptype = it.get("_property_type") or property_type(it.get("PropertyType"))
        status_name = ((it.get("BusinessStatus") or {}).get("Name") or "").strip().lower()
        status = STATUS.get(status_name, "available")
        price = money((it.get("RentPrice") or {}).get("Value"))
        area = number(it.get("NetArea")) or number(it.get("ListingArea"))
        lat, lon = _coord(it.get("Lat")), _coord(it.get("Lng"))
        # "Apartamento T2 / Loures, Camarate, Unhos e Apelação": council, then
        # parish or neighbourhood.
        where = (it.get("Title") or "").split(" / ", 1)[-1]
        council_txt, _, rest = where.partition(", ")
        council, parish = geo.place(council_txt, rest, lat, lon,
                                    extra_texts=tuple((it.get("Localization") or "").split(", ")))
        extras = []
        if it.get("Elevator"):
            extras.append("Elevador")
        if it.get("Parking"):
            extras.append("Garagem")
        listing = {
            "source": self.name, "external_id": ref, "url": url,
            "transaction_type": "rent", "property_type": ptype,
            "typology": typology(it.get("Title"), bedrooms=it.get("Rooms")),
            "rooms_total": None, "area_sqm": area,
            "floor": str(it.get("Floor")) if it.get("Floor") not in (None, "") else None,
            "district": "Lisboa", "council": council, "parish": parish,
            "neighborhood": rest or None, "street": None,
            "is_private_owner": False,
            "agency_name": ((it.get("RealEstate") or {}).get("Name") or "ERA").strip(),
            "listed_at": None,
            "thumbnail_url": (it.get("_photos") or [None])[0],
            "latitude": lat, "longitude": lon,
            "energy_cert": (it.get("Ce") or "").strip() or None,
            "image_urls": it.get("_photos") or None,
            "extras": extras or None,
            "status": status,
        }
        snapshot = None
        if price and status != "rented":
            snapshot = {"price_eur": price, "price_per_sqm": per_sqm(price, area), "title": title}
        return listing, snapshot

    def _detail(self, d: dict):
        ref = str(d.get("Reference") or "").strip()
        if not ref:
            return None
        loc = d.get("LocalizationDetail") or {}
        desc = text(d.get("Description"))
        feats = [f.get("Name") if isinstance(f, dict) else str(f) for f in (d.get("OtherFeatures") or [])]
        council, parish = geo.place(loc.get("County"), loc.get("Parish"), extra_texts=(loc.get("Zone"),))
        listing = {"source": self.name, "external_id": ref, "description": desc,
                   "extras": extras_from_text(desc, " ".join(f for f in feats if f)) or None,
                   "_detail_only": True}
        if parish:
            listing |= {"council": council, "parish": parish}
        return listing, None
