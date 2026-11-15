"""Council and parish names that agree across portals.

Every portal spells places its own way: "Sto António dos Cavaleiros",
"Santo António dos Cavaleiros e Frielas", "União das freguesias de ...".
Imovirtual's names match INE and CAOP, so everything else is brought to
those, with two tools:

- locate(lat, lon): the CAOP parish that contains the point, from the outlines
  in site/geo/freguesias.geojson (the nine Grande Lisboa councils). Outlines
  are simplified to about 40 m, so a flat on a boundary can land next door.
- match_parish(council, text): the best CAOP name for a portal's own wording,
  when there are no coordinates.

Pure Python: 85 polygons and a few thousand points a night need no GIS stack.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from .config import SITE_SRC

GEOJSON = SITE_SRC / "geo" / "freguesias.geojson"

COUNCILS = ["Lisboa", "Odivelas", "Loures", "Amadora", "Oeiras", "Sintra", "Cascais",
            "Vila Franca de Xira", "Mafra"]


def fold(text: str | None) -> str:
    """Lower case, no accents, single spaces: 'São João' -> 'sao joao'."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\b(sta|sto)\b\.?", lambda m: "santa" if m.group(1) == "sta" else "santo", text)
    text = re.sub(r"\bs\.\s*", "sao ", text)
    text = re.sub(r"\bn\.?\s*sra\.?\b", "nossa senhora", text)
    text = re.sub(r"^uniao das freguesias de\s+", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def slug(text: str) -> str:
    return fold(text).replace(" ", "-")


def canonical_council(text: str | None) -> str | None:
    f = fold(text)
    for c in COUNCILS:
        if fold(c) == f:
            return c
    return text.strip() if text else None


@lru_cache(maxsize=1)
def _parishes() -> list[dict]:
    try:
        data = json.loads(GEOJSON.read_text())
    except FileNotFoundError:
        return []
    out = []
    for feat in data.get("features", []):
        g = feat.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", [])
        rings = [poly for poly in polys if poly]
        xs = [x for poly in rings for x, _ in poly[0]]
        ys = [y for poly in rings for _, y in poly[0]]
        if not xs:
            continue
        out.append({
            "council": feat["properties"]["council"],
            "parish": feat["properties"]["parish"],
            "polys": rings,
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "folded": fold(feat["properties"]["parish"]),
        })
    return out


def _in_ring(x: float, y: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def locate(lat: float | None, lon: float | None) -> tuple[str, str] | None:
    """(council, parish) containing the point, or None outside the outlines."""
    if lat is None or lon is None:
        return None
    for p in _parishes():
        x0, y0, x1, y1 = p["bbox"]
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        for poly in p["polys"]:
            if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, hole) for hole in poly[1:]):
                return p["council"], p["parish"]
    return None


def match_parish(council: str | None, text: str | None) -> str | None:
    """The CAOP parish of `council` that best matches a portal's wording, or
    None when nothing is close. 'Sto António dos Cavaleiros' finds 'Santo
    António dos Cavaleiros e Frielas'; 'Ribeirada' (a neighbourhood) finds
    nothing."""
    f = fold(text)
    if not f:
        return None
    cands = [p for p in _parishes() if p["council"] == council]
    for p in cands:
        if p["folded"] == f:
            return p["parish"]
    # A union of parishes contains each old parish's name: "Pontinha" is in
    # "Pontinha e Famões". Prefer the longest old-parish name that matches.
    best, best_len = None, 0
    for p in cands:
        parts = re.split(r"\s(?:e)\s|,\s*", p["folded"])
        for part in [p["folded"], *parts]:
            part = part.strip()
            if len(part) >= 4 and (part == f or f.startswith(part + " ") or part in f.split(" e ")
                                   or f in part.split(" e ")):
                if len(part) > best_len:
                    best, best_len = p["parish"], len(part)
    return best


def place(council: str | None, parish_text: str | None, lat=None, lon=None,
          extra_texts: tuple = ()) -> tuple[str | None, str | None]:
    """Council and parish for an advert: from the point when there is one,
    else from the portal's words."""
    hit = locate(lat, lon)
    if hit:
        return hit
    council = canonical_council(council)
    for text in (parish_text, *extra_texts):
        parish = match_parish(council, text)
        if parish:
            return council, parish
    return council, None
