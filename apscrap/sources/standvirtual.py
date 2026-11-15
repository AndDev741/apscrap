"""Standvirtual (OLX Group Portugal, same platform as Imovirtual).

Pages are Next.js renders, but unlike Imovirtual the search results are not
plain props: they sit in pageProps.urqlState, a cache of GraphQL answers keyed
by query hash, each answer a JSON *string*. The hashes change whenever the
site changes its query text, so entries are found by what they hold:

  advertSearch   the result list: edges[].node (organic), topads[].node (paid),
                 totalCount, pageInfo {pageSize, currentOffset},
                 appliedFilters, alternativeLinks (the "body-types" block
                 carries the model's segment mix with counts)
  filters        the filter catalogue: states[] with filterId
                 filter_enum_make (every make) and one filter_enum_model state
                 per make (conditions[0].value = make slug)

An advert page carries pageProps.advert directly: parametersDict (every
attribute as value + label), images.photos, seller, price.

This module is deliberately not in SOURCES. That registry drives the nightly
crawl, and Standvirtual's terms forbid repeated, systematic extraction, so
nothing here may run on a schedule. apscrap/cars.py calls it when a person
presses a button.
"""
import json
import math
import re
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

BASE = "https://www.standvirtual.com"
HOST = "www.standvirtual.com"

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ADVERT_PATH = re.compile(r"^/carros/anuncio/[A-Za-z0-9._-]+\.html$")
_INT = re.compile(r"\d+")

# robots.txt for User-agent: * as read on 2026-11-15. Checked before every
# request so that a future code path cannot wander into these by accident.
_ROBOTS_PREFIXES = (
    "/adminpanel/", "/authentication", "/account/", "/myaccount/", "/adprint/",
    "/ad2/", "/ad/leaflet/", "/ad/contact/", "/payment/", "/adding/confirm/",
    "/adding/confirmpage/", "/i2/", "/m/ad/abuse/", "/ajax/", "/api/",
)
_ROBOTS_ANYWHERE = ("/rss/", "/payment/", "/new-confirmation")
_ROBOTS_CATALOG = re.compile(r"^/catalog/[^/]*/[^/]*/")

# Search pages ask for used cars only, newest first. A fixed order keeps the
# pages from reshuffling between requests (the default "relevance" order moves
# bumped adverts around), so five pages are the five newest pages.
_SEARCH_PARAMS = {"search[new_used]": "used", "search[order]": "created_at_first:desc"}

# Gallery size the advert page itself serves (srcset 768x0 = 768 wide, keep
# aspect), and the card size the search page uses.
_MEDIUM = ";s=768x0"
_THUMB = ";s=320x240"
MAX_IMAGES = 12

# Normalisation onto the values in the cars column comments. Keys are the
# portal's own slugs; labels are matched too, lower-cased without accents.
FUEL = {
    "gaz": "gasolina", "gasolina": "gasolina",
    "diesel": "gasoleo",
    "hibride-gaz": "hibrido", "hibrido (gasolina)": "hibrido",
    "hibride-diesel": "hibrido", "hibrido (diesel)": "hibrido",
    "plugin-hybrid": "hibrido_plugin", "hibrido plug-in": "hibrido_plugin",
    "electric": "eletrico", "eletrico": "eletrico",
    "gpl": "gpl",
    # GNC (natural gas) and hydrogen have no value in the column; left NULL.
}
GEARBOX = {"manual": "manual", "automatic": "automatica", "automatica": "automatica"}
BODY = {
    "combi": "carrinha", "carrinha": "carrinha",
    "suv": "suv", "suv / tt": "suv",
    "city-car": "citadino", "citadino": "citadino",
    "mini": "citadino", "pequeno citadino": "citadino",
    "compact": "utilitario", "utilitario": "utilitario",
    "minivan": "monovolume", "monovolume": "monovolume",
    "sedan": "sedan",
    "coupe": "coupe",
    "cabrio": "cabrio",
}

_ACCENTS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")


def _dig(obj: Any, *keys, default=None):
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k)
    return obj if obj is not None else default


def _key(text: str | None) -> str:
    return (text or "").strip().lower().translate(_ACCENTS)


def _norm(table: dict, value: str | None, label: str | None, other: str | None = None):
    for candidate in (value, label):
        hit = table.get(_key(candidate))
        if hit:
            return hit
    return other if (value or label) else None


def _int(value) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    m = _INT.search(str(value or "").replace(" ", "").replace(".", ""))
    return int(m.group()) if m else None


def _price(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _sized(url: str | None, size: str) -> str | None:
    if not url:
        return None
    return url.split(";s=")[0] + size


def _flat_values(state: dict) -> list[dict]:
    return [v for group in state.get("values") or [] for v in group.get("values") or []]


def check_slug(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise ValueError(f"not a Standvirtual slug: {slug!r}")
    return slug


def robots_allowed(url: str) -> bool:
    parts = urlsplit(url)
    if parts.hostname not in (HOST, "standvirtual.com"):
        return False
    path = parts.path or "/"
    if path.startswith(_ROBOTS_PREFIXES) or _ROBOTS_CATALOG.match(path):
        return False
    return not any(s in path for s in _ROBOTS_ANYWHERE)


class Standvirtual:
    name = "standvirtual"

    # -- URLs --------------------------------------------------------------

    def search_url(self, make_slug: str, model_slug: str, page: int = 1) -> str:
        """Used cars of one make and model, whole country, newest first.
        The site's canonical form of the same search is
        /carros/usados/<make>/<model>?page=N&search[order]=...; this one is
        the query-parameter form, verified on 2026-11-15 to apply new_used."""
        params = dict(_SEARCH_PARAMS)
        if page > 1:
            params["page"] = str(page)
        return f"{BASE}/carros/{check_slug(make_slug)}/{check_slug(model_slug)}?{urlencode(params)}"

    def catalog_url(self) -> str:
        """Any search page carries the full make/model catalogue in its filters."""
        return f"{BASE}/carros?{urlencode({'search[new_used]': 'used'})}"

    def advert_url(self, url: str) -> str:
        """Normalise a pasted advert link, or raise ValueError if it is not one."""
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
        if host not in (HOST, "standvirtual.com", "m.standvirtual.com"):
            raise ValueError("o link não é do Standvirtual")
        if not _ADVERT_PATH.match(parts.path):
            raise ValueError("o link não é de um anúncio de carro (/carros/anuncio/...)")
        return f"{BASE}{parts.path}"

    # -- extraction --------------------------------------------------------

    def extract_next_data(self, html: str) -> dict | None:
        m = _NEXT_DATA.search(html or "")
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    def search_payload(self, html: str) -> dict | None:
        """Decode the urql cache into one dict: {"advertSearch": ..., "filters": ...}."""
        data = self.extract_next_data(html)
        merged: dict = {}
        for entry in (_dig(data, "props", "pageProps", "urqlState", default={}) or {}).values():
            raw = entry.get("data") if isinstance(entry, dict) else None
            if not isinstance(raw, str):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                merged.update(obj)
        return merged if isinstance(merged.get("advertSearch"), dict) else None

    def advert_payload(self, html: str) -> dict | None:
        ad = _dig(self.extract_next_data(html), "props", "pageProps", "advert")
        return ad if isinstance(ad, dict) and ad.get("id") else None

    # -- search pages ------------------------------------------------------

    @staticmethod
    def applied(payload: dict) -> dict:
        """{filter name: (slug, label)} as the site applied them, e.g.
        filter_enum_make -> ("toyota", "Toyota"). Lets the caller check the
        site searched for what was asked rather than silently widening."""
        out = {}
        for f in _dig(payload, "advertSearch", "appliedFilters", default=[]) or []:
            labels = f.get("labels") or []
            out[f.get("name")] = (f.get("value"), labels[0] if labels else None)
        return out

    def parse_search(self, payload: dict, make_slug: str | None = None,
                     model_slug: str | None = None) -> list[dict]:
        """Car dicts (the cars columns plus price_eur) for every card on the page.

        Paid top ads come along with the organic results; with make_slug and
        model_slug given, any card of another make or model is dropped."""
        search = payload.get("advertSearch") or {}
        cars, seen = [], set()
        for edge in (search.get("edges") or []) + (search.get("topads") or []):
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                continue
            car, slugs = self._card(node)
            if car is None or car["external_id"] in seen:
                continue
            if make_slug and model_slug and slugs != (make_slug, model_slug):
                continue
            seen.add(car["external_id"])
            cars.append(car)
        return cars

    def _card(self, node: dict) -> tuple[dict | None, tuple]:
        external_id = str(node.get("id") or "").strip()
        url = node.get("url")
        if not external_id or not url:
            return None, ()
        params = {
            p.get("key"): (p.get("value"), p.get("displayValue"))
            for p in node.get("parameters") or [] if isinstance(p, dict)
        }

        def val(key):
            return params.get(key, (None, None))[0]

        def label(key):
            return params.get(key, (None, None))[1]

        city = _dig(node, "location", "city", "name")
        region = _dig(node, "location", "region", "name")
        seller = _dig(node, "seller", "__typename")
        thumb = node.get("thumbnail") or {}
        car = {
            "source": self.name,
            "external_id": external_id,
            "url": url,
            "title": node.get("title"),
            "make": label("make"),
            "model": label("model"),
            "version": label("version"),
            "year": _int(val("first_registration_year")),
            "first_registration": None,      # only the advert page has the month
            "km": _int(val("mileage")),
            "fuel": _norm(FUEL, val("fuel_type"), label("fuel_type")),
            "gearbox": _norm(GEARBOX, val("gearbox"), label("gearbox")),
            "body_type": None,               # not on search cards
            "engine_cc": _int(val("engine_capacity")),
            "power_hp": _int(val("engine_power")),
            "co2_gkm": None,
            "seats": None,
            "doors": None,
            "location": ", ".join(x for x in (city, region) if x) or None,
            "thumbnail_url": thumb.get("x1") or thumb.get("x2"),
            "image_urls": None,
            "is_private_seller": {"PrivateSeller": True, "ProfessionalSeller": False}.get(seller),
            "price_eur": _price(_dig(node, "price", "amount", "units"))
                         or _price(_dig(node, "price", "amount", "value")),
        }
        return car, (val("make"), val("model"))

    @staticmethod
    def total_count(payload: dict) -> int:
        return int(_dig(payload, "advertSearch", "totalCount", default=0) or 0)

    @staticmethod
    def total_pages(payload: dict) -> int:
        size = int(_dig(payload, "advertSearch", "pageInfo", "pageSize", default=32) or 32)
        return max(1, math.ceil(Standvirtual.total_count(payload) / size))

    @staticmethod
    def segment_mix(payload: dict) -> dict[str, int]:
        """Segment counts for the searched model, from the "body-types" link
        block, e.g. {"utilitario": 51, "sedan": 24}. Cards carry no body type,
        so this is the only body information a search page gives. The counts
        need not add up to totalCount: adverts without a segment are left out."""
        mix: dict[str, int] = {}
        for block in _dig(payload, "advertSearch", "alternativeLinks", default=[]) or []:
            if block.get("name") != "body-types":
                continue
            for link in block.get("links") or []:
                qs = parse_qs(urlsplit(link.get("url") or "").query)
                slug = (qs.get("search[filter_enum_body_type]") or [None])[0]
                body = _norm(BODY, slug, None, other="outro")
                if body:
                    mix[body] = mix.get(body, 0) + int(link.get("counter") or 0)
        return mix

    @staticmethod
    def parse_catalog(payload: dict) -> list[dict]:
        """[{"make", "make_slug", "n", "models": [{"model", "model_slug", "n"}]}].
        n is the number of adverts (new and used) on the site when fetched."""
        states = _dig(payload, "filters", "states", default=[]) or []
        makes, models = [], {}
        for st in states:
            fid, cond = st.get("filterId"), st.get("conditions") or []
            if fid == "filter_enum_make" and not cond:
                makes = _flat_values(st)
            elif fid == "filter_enum_model" and len(cond) == 1 \
                    and cond[0].get("filterId") == "filter_enum_make":
                models[cond[0].get("value")] = _flat_values(st)
        return [
            {
                "make": mk.get("name"),
                "make_slug": mk.get("id"),
                "n": mk.get("counter"),
                "models": [
                    {"model": md.get("name"), "model_slug": md.get("id"), "n": md.get("counter")}
                    for md in models.get(mk.get("id"), [])
                ],
            }
            for mk in makes if mk.get("id") and mk.get("name")
        ]

    # -- advert pages ------------------------------------------------------

    @staticmethod
    def _params(ad: dict) -> dict[str, tuple]:
        out = {}
        for key, spec in (ad.get("parametersDict") or {}).items():
            vals = (spec or {}).get("values") or []
            if vals and isinstance(vals[0], dict):
                out[key] = (vals[0].get("value"), vals[0].get("label"))
        return out

    def advert_slugs(self, ad: dict) -> tuple[str | None, str | None]:
        p = self._params(ad)
        return p.get("make", (None,))[0], p.get("model", (None,))[0]

    def parse_advert(self, ad: dict) -> dict:
        """The same dict as a search card, with what only the advert page has:
        first registration month, segment, seats, doors, images, and CO2 when
        the seller filled it in (rare: absent from the sample advert)."""
        p = self._params(ad)

        def val(key):
            return p.get(key, (None, None))[0]

        def label(key):
            return p.get(key, (None, None))[1]

        year = _int(val("first_registration_year"))
        month = _int(val("first_registration_month"))
        first_reg = date(year, month, 1) if year and month and 1 <= month <= 12 else None

        co2 = None
        for key in p:
            if "co2" in key.lower():
                co2 = _int(p[key][0]) or _int(p[key][1])
                if co2:
                    break

        photos = [ph.get("url") for ph in _dig(ad, "images", "photos", default=[]) or []
                  if isinstance(ph, dict) and ph.get("url")]
        seller = ad.get("seller") or {}
        loc = seller.get("location") or ad.get("location") or {}
        city = loc.get("city") if isinstance(loc.get("city"), str) else _dig(loc, "city", "name")
        region = loc.get("region") if isinstance(loc.get("region"), str) else _dig(loc, "region", "name")
        seller_type = (seller.get("type") or "").upper()

        return {
            "source": self.name,
            "external_id": str(ad.get("id")),
            "url": ad.get("url"),
            "title": ad.get("title"),
            "make": label("make"),
            "model": label("model"),
            "version": val("version_label") or label("version"),
            "year": year,
            "first_registration": first_reg,
            "km": _int(val("mileage")),
            "fuel": _norm(FUEL, val("fuel_type"), label("fuel_type")),
            "gearbox": _norm(GEARBOX, val("gearbox"), label("gearbox")),
            "body_type": _norm(BODY, val("body_type"), label("body_type"), other="outro"),
            "engine_cc": _int(val("engine_capacity")),
            "power_hp": _int(val("engine_power")),
            "co2_gkm": co2,
            "seats": _int(val("nr_seats")),
            "doors": _int(val("door_count")),
            "location": ", ".join(x for x in (city, region) if x) or None,
            "thumbnail_url": _sized(photos[0], _THUMB) if photos else None,
            "image_urls": [_sized(u, _MEDIUM) for u in photos[:MAX_IMAGES]] or None,
            "is_private_seller": {"PRIVATE": True, "PROFESSIONAL": False,
                                  "BUSINESS": False}.get(seller_type),
            "price_eur": _price(_dig(ad, "price", "value")),
        }
