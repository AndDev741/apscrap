"""Small parsing helpers shared by the portal parsers."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

_TYP = re.compile(r"\bT\s?(\d{1,2})\b", re.I)
_MONEY = re.compile(r"\d[\d.\s ]*(?:,\d+)?")

# Portal words for the two kinds of home we track. Everything else (rooms,
# shops, plots, garages) is skipped at parse time.
FLAT_WORDS = ("apartamento", "duplex", "triplex", "estudio", "estúdio", "loft",
              "penthouse", "cobertura", "flat", "andar")
HOUSE_WORDS = ("moradia", "vivenda", "casa", "house", "andar moradia", "chalet")


def typology(*texts, bedrooms=None) -> str | None:
    """'T2' from a title or slug; else from the bedroom count. T6 and above
    collapse to T6, like the views expect."""
    for text in texts:
        m = _TYP.search(text or "")
        if m:
            return f"T{min(int(m.group(1)), 6)}"
    try:
        n = int(bedrooms)
    except (TypeError, ValueError):
        return None
    return f"T{min(max(n, 0), 6)}"


def property_type(*texts) -> str | None:
    """'flat' or 'house' from a portal's type wording, or None."""
    for text in texts:
        t = (text or "").lower()
        if not t:
            continue
        if "andar moradia" in t or "andar-moradia" in t:
            return "flat"
        if any(w in t for w in ("moradia", "vivenda", "chalet")):
            return "house"
        if any(w in t for w in FLAT_WORDS):
            return "flat"
    return None


def money(text) -> float | None:
    """1100.0 from '1.100 €', '1 100€', 1100 or '1.100,50 €'."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    m = _MONEY.search(str(text))
    if not m:
        return None
    raw = re.sub(r"[.\s ]", "", m.group(0)).replace(",", ".")
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v > 0 else None


def number(text) -> float | None:
    """60.0 from '60', '60,5', '60 m²' or 60."""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    m = re.search(r"\d+(?:[.,]\d+)?", str(text))
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    return v if v > 0 else None


def per_sqm(price, area) -> float | None:
    if price and area:
        return round(float(price) / float(area), 2)
    return None


def text(raw: str | None, limit: int = 4000) -> str | None:
    """Plain text from a description that may hold <br/> and entities."""
    if not raw:
        return None
    t = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t).strip()
    return t[:limit] or None


def ts(value) -> datetime | None:
    if not value or not isinstance(value, str) or value.startswith("0001"):
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    # Portals that leave the zone out mean Lisbon time, which is UTC half the
    # year and an hour off the other half: close enough for "changed since".
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# Extras the app filters on, from words in a description. Same names as
# Imovirtual's structured list.
EXTRAS_TEXT = [
    ("Garagem", re.compile(r"\b(garagem|box|lugar de estacionamento|parqueamento)\b", re.I)),
    ("Elevador", re.compile(r"\belevador", re.I)),
    ("Varanda", re.compile(r"\b(varanda|terra[çc]o|marquise)", re.I)),
    ("Ar condicionado", re.compile(r"\bar condicionado\b", re.I)),
    ("Mobilado", re.compile(r"\b(mobilad[oa]|equipad[oa] e mobilad)", re.I)),
    ("Arrecadação", re.compile(r"\barrecada[çc][ãa]o", re.I)),
    ("Jardim", re.compile(r"\bjardim\b", re.I)),
    ("Piscina", re.compile(r"\bpiscina\b", re.I)),
]


def extras_from_text(*texts) -> list[str]:
    blob = " ".join(t for t in texts if t)
    return [name for name, rx in EXTRAS_TEXT if rx.search(blob)]
