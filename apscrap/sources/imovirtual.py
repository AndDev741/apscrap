"""Imovirtual (OLX Group Portugal).

Every page is a Next.js render with the full result set sitting in a
__NEXT_DATA__ script tag, so there is no HTML parsing here at all. We read the
JSON the page was built from.
"""
import json
import re
from datetime import datetime
from typing import Any, Iterator

BASE = "https://www.imovirtual.com"

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)
# Portuguese typology: T0 studio, T1 one bedroom, and so on.
_TYPOLOGY = re.compile(r"\bT\s?([0-6])\b", re.I)

_WORD_TO_INT = {
    "ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
    "MORE": None,
}

# The portal spells the top grades out; everyone in Portugal writes A+ / A++.
_ENERGY = {"APLUS": "A+", "APLUSPLUS": "A++", "AP": "A+"}

_FLOOR = {
    "CELLAR": "cave", "GROUND": "0", "FIRST": "1", "SECOND": "2",
    "THIRD": "3", "FOURTH": "4", "FIFTH": "5", "SIXTH": "6",
    "SEVENTH": "7", "EIGHTH": "8", "NINTH": "9", "TENTH": "10",
    "ABOVE_TENTH": ">10", "GARRET": "sotao",
}

TRANSACTIONS = {"rent": "arrendar", "sale": "comprar"}
# The portal says SELL in its payload; we use "sale" everywhere so that the CLI
# flag, the URL builder and the database column all agree.
_TRANSACTION_OUT = {"RENT": "rent", "SELL": "sale"}
PROPERTY_TYPES = {"flat": "apartamento", "house": "moradia"}


def _dig(obj: Any, *keys, default=None):
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k)
    return obj if obj is not None else default


def _money(node) -> float | None:
    v = _dig(node, "value")
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace(" ", "T", 1) if " " in value else value
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _slim_item(item: dict) -> dict:
    """Drop what we never read before it reaches raw_pages.

    Photo URLs were over half of every stored page, and the agency and owner
    blocks carry logos and profile links. Keeping all of it for every advert
    every night would grow the database by ~15 GB a year. One thumbnail and the
    agency name are all anything downstream uses."""
    slim = {k: v for k, v in item.items() if k not in ("images", "agency", "advertOwner", "links")}
    images = item.get("images") or []
    if images and isinstance(images[0], dict):
        slim["images"] = [{"medium": images[0].get("medium")}]
    agency = item.get("agency")
    if isinstance(agency, dict):
        slim["agency"] = {k: agency.get(k) for k in ("id", "name", "type")}
    return slim


def _energy(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().upper()
    return _ENERGY.get(key, key)


class Imovirtual:
    name = "imovirtual"

    def search_url(self, transaction: str, property_type: str, location: str, page: int = 1) -> str:
        url = (
            f"{BASE}/pt/resultados/{TRANSACTIONS[transaction]}"
            f"/{PROPERTY_TYPES[property_type]}/{location}"
        )
        return f"{url}?page={page}" if page > 1 else url

    def detail_url(self, slug: str) -> str:
        return f"{BASE}/pt/anuncio/{slug}"

    # -- extraction ------------------------------------------------------

    def extract_next_data(self, html: str) -> dict | None:
        m = _NEXT_DATA.search(html)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    def search_payload(self, html: str) -> dict | None:
        """Trim the Next.js blob down to the part worth keeping in raw_pages."""
        data = self.extract_next_data(html)
        ads = _dig(data, "props", "pageProps", "data", "searchAds")
        if not ads:
            return None
        return {
            "items": [_slim_item(it) for it in ads.get("items", []) if isinstance(it, dict)],
            "pagination": ads.get("pagination", {}),
            "stats": ads.get("stats", []),
        }

    def detail_payload(self, html: str) -> dict | None:
        data = self.extract_next_data(html)
        ad = _dig(data, "props", "pageProps", "ad")
        if not ad:
            return None
        keep = (
            "id", "location", "characteristics", "features", "description",
            "createdAt", "modifiedAt", "market", "advertiserType", "agency",
        )
        return {k: ad.get(k) for k in keep if k in ad}

    # -- normalisation ---------------------------------------------------

    def parse_search_items(self, payload: dict) -> Iterator[tuple[dict, dict]]:
        """Yield (listing_attrs, snapshot_attrs) for every advert on the page."""
        for item in payload.get("items", []):
            external_id = str(item.get("id") or "").strip()
            slug = item.get("slug")
            if not external_id or not slug:
                continue

            title = item.get("title") or ""
            price = _money(item.get("totalPrice")) or _money(item.get("rentPrice"))
            area = item.get("areaInSquareMeters")
            per_sqm = _money(item.get("pricePerSquareMeter"))
            if per_sqm is None and price and area:
                per_sqm = round(price / float(area), 2)

            geo = {
                lvl.get("locationLevel"): lvl.get("name")
                for lvl in _dig(item, "location", "reverseGeocoding", "locations", default=[])
                if isinstance(lvl, dict)
            }

            # The T-rating in the title is the reliable source. Where the advertiser
            # left it out we fall back to the portal's room count minus the living
            # room, and flag it, because that count is inconsistent across agencies.
            typology_match = _TYPOLOGY.search(title)
            rooms_total = _WORD_TO_INT.get(item.get("roomsNumber"))
            if typology_match:
                typology, inferred = f"T{typology_match.group(1)}", False
            elif rooms_total and rooms_total >= 1:
                typology, inferred = f"T{min(rooms_total - 1, 6)}", True
            else:
                typology, inferred = None, False

            listing = {
                "source": self.name,
                "external_id": external_id,
                "url": self.detail_url(slug),
                "transaction_type": _TRANSACTION_OUT.get(
                    (item.get("transaction") or "").upper(),
                    (item.get("transaction") or "").lower() or None,
                ),
                "property_type": (item.get("estate") or "").lower() or None,
                "typology": typology,
                "typology_inferred": inferred,
                "rooms_total": rooms_total,
                "area_sqm": float(area) if area else None,
                "floor": _FLOOR.get(item.get("floorNumber")),
                "district": geo.get("district"),
                "council": geo.get("council"),
                "parish": geo.get("parish"),
                "neighborhood": geo.get("neighborhood"),
                "street": _dig(item, "location", "address", "street", "name"),
                "is_private_owner": item.get("isPrivateOwner"),
                "agency_name": _dig(item, "agency", "name"),
                "listed_at": _ts(item.get("createdAtFirst") or item.get("dateCreated")),
                "thumbnail_url": next(
                    (img.get("medium") for img in item.get("images") or [] if isinstance(img, dict)),
                    None,
                ),
            }
            snapshot = {
                "price_eur": price,
                "price_per_sqm": per_sqm,
                "title": title,
                "is_promoted": bool(item.get("isPromoted")),
                "pushed_up_at": _ts(item.get("pushedUpAt")),
            }
            yield listing, snapshot

    def parse_detail(self, payload: dict) -> dict:
        """Extra attributes the search page does not carry."""
        chars = {
            c.get("key"): c.get("value")
            for c in payload.get("characteristics") or []
            if isinstance(c, dict)
        }
        coords = _dig(payload, "location", "coordinates", default={})
        floor_raw = (chars.get("floor_no") or "").replace("floor_", "")

        def num(key, cast=float):
            try:
                return cast(chars[key])
            except (KeyError, TypeError, ValueError):
                return None

        return {
            "latitude": coords.get("latitude"),
            "longitude": coords.get("longitude"),
            "build_year": num("build_year", int),
            "energy_cert": _energy(chars.get("energy_certificate")),
            "condition": chars.get("construction_status"),
            "deposit_eur": num("deposit"),
            "floor": _FLOOR.get(floor_raw.upper()) or (floor_raw or None),
        }

    @staticmethod
    def total_pages(payload: dict) -> int:
        return int(_dig(payload, "pagination", "totalPages", default=1) or 1)

    @staticmethod
    def total_items(payload: dict) -> int:
        return int(_dig(payload, "pagination", "totalItems", default=0) or 0)
