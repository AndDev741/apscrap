"""Cars: one make and model at a time, fetched only when a person asks.

Standvirtual's terms (Termos e Condições para Particulares, May 2026) forbid
"download, agregação ou processamento repetido e sistemático de dados" and
claim database rights, while allowing personal download of insubstantial
parts. So in this module:

- nothing runs on a schedule, and nothing is wired into the CLI or the daily
  run; the web layer calls these functions when someone presses a button;
- refresh() reads at most MAX_PAGES search pages of one model, and refuses to
  run again within REFRESH_MIN_GAP of the last fetch unless forced;
- add_from_url() reads exactly one advert page;
- catalog() reads one search page at most every CATALOG_MAX_AGE, and refresh()
  updates the same cache from the first page it already has.

Every request goes through one Fetcher shared by the whole process (3 s gap,
fixed user agent), one request at a time, and is checked against robots.txt
before it is sent. Nothing goes to raw_pages or
crawl_runs: parse.py replays raw_pages through SOURCES, and export.py reads
the newest crawl_runs row as the last nightly property crawl.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

import httpx
from psycopg.types.json import Jsonb

from .db import connect
from .sources.base import Fetcher
from .sources.standvirtual import Standvirtual, check_slug, robots_allowed

log = logging.getLogger(__name__)

SV = Standvirtual()
LISBON = ZoneInfo("Europe/Lisbon")

MAX_PAGES = 5                          # hard cap per refresh, force or not
REFRESH_MIN_GAP = timedelta(days=7)
CATALOG_KEY = "car_catalog"
CATALOG_MAX_AGE = timedelta(days=30)
CATALOG_RETRY = timedelta(hours=1)     # after a failed fetch, not on every page view
# Segment mix per model from the search page, {"<model_id>": {"utilitario": 51, ...}}.
# Search cards carry no body type, so cars.body_type stays NULL for them rather
# than guessing; this is what depreciation() uses to place a model in a segment.
SEGMENTS_KEY = "car_segments"
DOUBLE_CLICK = timedelta(minutes=10)   # same advert pasted again: no new fetch

MIN_CARS = 8
MIN_PER_AGE = 3
SWEET_SPOT_DROP = 0.10
# Past ~15 years a car's price is scrap value or collector interest, not
# depreciation: a model like the Corolla has 30-year-old adverts at 3 000 €
# that would bend the whole curve. The fit stays on the ages anyone buys at.
MAX_FIT_AGE = 15

_LOCK_NS = 0x5643                      # advisory-lock namespace: one refresh per model at a time

CAR_COLUMNS = (
    "source", "external_id", "url", "title", "make", "model", "version", "year",
    "first_registration", "km", "fuel", "gearbox", "body_type", "engine_cc",
    "power_hp", "co2_gkm", "seats", "doors", "location", "thumbnail_url",
    "image_urls", "is_private_seller",
)

# A NULL never overwrites a value: search cards lack what an advert page gave
# (segment, seats, photos, registration month) and must not erase it.
_UPSERT = f"""
INSERT INTO cars ({", ".join(CAR_COLUMNS)}, model_id, first_seen_at, last_seen_at)
VALUES ({", ".join(f"%({c})s" for c in CAR_COLUMNS)}, %(model_id)s, now(), now())
ON CONFLICT (source, external_id) DO UPDATE SET
    {", ".join(f"{c} = COALESCE(EXCLUDED.{c}, cars.{c})"
               for c in CAR_COLUMNS if c not in ("source", "external_id"))},
    model_id = COALESCE(EXCLUDED.model_id, cars.model_id),
    last_seen_at = now()
RETURNING id, (xmax = 0) AS inserted
"""

_TODAY = "(now() AT TIME ZONE 'Europe/Lisbon')::date"


# -- small helpers -------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age(iso: str | None) -> timedelta:
    if not iso:
        return timedelta.max
    try:
        return _now() - datetime.fromisoformat(iso)
    except ValueError:
        return timedelta.max


# Fetcher's delay holds only between requests of the same instance. One
# instance per call would let catalog() and refresh(), or two people pressing
# buttons at once, hit the site back to back, so every car request in the
# process goes through this one, under a lock. (Separate worker processes each
# get their own; the app runs as one.)
_fetcher: Fetcher | None = None
_fetch_lock = threading.Lock()


def _get(url: str) -> httpx.Response:
    global _fetcher
    if not robots_allowed(url):
        raise ValueError(f"robots.txt does not allow {url}")
    with _fetch_lock:
        if _fetcher is None:
            _fetcher = Fetcher()
        return _fetcher.get(url)


def _setting(conn, key: str) -> dict | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def _put_setting(conn, key: str, value: dict, merge: bool = False) -> None:
    new = "app_settings.value || EXCLUDED.value" if merge else "EXCLUDED.value"
    conn.execute(
        f"""INSERT INTO app_settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = {new}, updated_at = now()""",
        (key, Jsonb(value)),
    )


def _save_catalog(conn, makes: list[dict]) -> None:
    stamp = _now().isoformat()
    _put_setting(conn, CATALOG_KEY, {"fetched_at": stamp, "attempted_at": stamp, "makes": makes})


def _pretty(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _names(makes: list[dict], make_slug: str, model_slug: str) -> tuple[str, str]:
    """Display names from the cached catalogue. Without a catalogue (never
    fetched) the slugs stand in, and the first refresh puts the site's names in."""
    if not makes:
        return _pretty(make_slug), _pretty(model_slug)
    mk = next((m for m in makes if m.get("make_slug") == make_slug), None)
    if mk is None:
        raise ValueError(f"marca desconhecida no Standvirtual: {make_slug}")
    md = next((m for m in mk.get("models") or [] if m.get("model_slug") == model_slug), None)
    if md is None:
        raise ValueError(f"modelo desconhecido no Standvirtual: {make_slug}/{model_slug}")
    return mk["make"], md["model"]


def _store(conn, car: dict, model_id: int | None) -> tuple[int, bool, float | None]:
    """Upsert one car and today's snapshot. Returns (id, inserted, previous price)."""
    row = conn.execute(_UPSERT, {**{c: car.get(c) for c in CAR_COLUMNS}, "model_id": model_id}).fetchone()
    prev = conn.execute(
        f"""SELECT price_eur FROM car_snapshots
             WHERE car_id = %s AND observed_date < {_TODAY} AND price_eur IS NOT NULL
             ORDER BY observed_date DESC LIMIT 1""",
        (row["id"],),
    ).fetchone()
    if car.get("price_eur") is not None:
        conn.execute(
            f"""INSERT INTO car_snapshots (car_id, observed_date, price_eur)
                VALUES (%s, {_TODAY}, %s)
                ON CONFLICT (car_id, observed_date) DO UPDATE SET price_eur = EXCLUDED.price_eur""",
            (row["id"], car["price_eur"]),
        )
    return row["id"], bool(row["inserted"]), (float(prev["price_eur"]) if prev else None)


# -- catalogue -----------------------------------------------------------------

def catalog() -> list[dict]:
    """Makes with their models, [{"make", "make_slug", "n", "models": [{"model",
    "model_slug", "n"}]}], n being the advert count when fetched. Cached in
    app_settings["car_catalog"]; one page fetch at most every 30 days."""
    with connect() as conn:
        cached = _setting(conn, CATALOG_KEY) or {}
    makes = cached.get("makes") or []
    if makes and _age(cached.get("fetched_at")) < CATALOG_MAX_AGE:
        return makes
    if _age(cached.get("attempted_at")) < CATALOG_RETRY:
        return makes

    fresh: list[dict] = []
    try:
        resp = _get(SV.catalog_url())
        payload = SV.search_payload(resp.text) if resp.status_code == 200 else None
        fresh = SV.parse_catalog(payload) if payload else []
        if not fresh:
            log.warning("car catalogue: nothing readable (HTTP %s)", resp.status_code)
    except httpx.HTTPError as exc:
        log.warning("car catalogue fetch failed: %s", exc)

    with connect() as conn:
        if fresh:
            _save_catalog(conn, fresh)
        else:
            _put_setting(conn, CATALOG_KEY, {**cached, "attempted_at": _now().isoformat()})
        conn.commit()
    return fresh or makes


# -- following and fetching ----------------------------------------------------

def follow(make_slug: str, model_slug: str, user_id: int | None) -> dict:
    """Start following a model. No request is sent; refresh() does that."""
    check_slug(make_slug)
    check_slug(model_slug)
    with connect() as conn:
        make, model = _names((_setting(conn, CATALOG_KEY) or {}).get("makes") or [],
                             make_slug, model_slug)
        params = {"make": make, "model": model, "label": f"{make} {model}",
                  "make_slug": make_slug, "model_slug": model_slug, "user": user_id}
        existing = conn.execute(
            "SELECT id FROM car_models WHERE make_slug = %s AND model_slug = %s",
            (make_slug, model_slug),
        ).fetchone()
        if existing:
            row = conn.execute(
                """UPDATE car_models
                      SET followed_by = CASE WHEN active THEN followed_by ELSE %(user)s END,
                          followed_at = CASE WHEN active THEN followed_at ELSE now() END,
                          active = true
                    WHERE id = %(id)s RETURNING *""",
                {**params, "id": existing["id"]},
            ).fetchone()
        else:
            row = conn.execute(
                """INSERT INTO car_models (make, model, label, make_slug, model_slug, followed_by)
                   VALUES (%(make)s, %(model)s, %(label)s, %(make_slug)s, %(model_slug)s, %(user)s)
                   ON CONFLICT (make, model) DO UPDATE SET
                       make_slug   = EXCLUDED.make_slug,
                       model_slug  = EXCLUDED.model_slug,
                       followed_by = CASE WHEN car_models.active THEN car_models.followed_by
                                          ELSE EXCLUDED.followed_by END,
                       followed_at = CASE WHEN car_models.active THEN car_models.followed_at
                                          ELSE now() END,
                       active      = true
                   RETURNING *""",
                params,
            ).fetchone()
        conn.commit()
    return dict(row)


def refresh(model_id: int, max_pages: int = MAX_PAGES, force: bool = False) -> dict:
    """Fetch up to max_pages (never more than 5) search pages of one followed
    model, newest adverts first, and record what they show today.

    Returns counts, or {"skipped": reason} when the model was fetched less than
    7 days ago (unless force) or another refresh of it is running, or
    {"error": ...} when not a single page could be read."""
    max_pages = max(1, min(int(max_pages or MAX_PAGES), MAX_PAGES))
    with connect() as conn:
        locked = conn.execute(
            "SELECT pg_try_advisory_lock(%s::int, %s::int) AS ok", (_LOCK_NS, model_id)
        ).fetchone()["ok"]
        conn.commit()
        if not locked:
            return {"skipped": "Este modelo já está a ser atualizado."}
        try:
            return _refresh_locked(conn, model_id, max_pages, force)
        finally:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(%s::int, %s::int)", (_LOCK_NS, model_id))
            conn.commit()


def _refresh_locked(conn, model_id: int, max_pages: int, force: bool) -> dict:
    m = conn.execute(
        "SELECT *, now() AS db_now FROM car_models WHERE id = %s", (model_id,)
    ).fetchone()
    conn.commit()
    if m is None:
        return {"skipped": "Modelo desconhecido."}
    if not m["make_slug"] or not m["model_slug"]:
        return {"skipped": "Modelo sem referência no Standvirtual."}
    last = m["last_fetched_at"]
    if last and not force and m["db_now"] - last < REFRESH_MIN_GAP:
        nxt = last + REFRESH_MIN_GAP
        return {
            "skipped": (f"{m['label']} foi atualizado a {last.astimezone(LISBON):%d/%m}. "
                        f"Os termos do Standvirtual não permitem recolha sistemática, por isso "
                        f"o próximo pedido fica disponível a {nxt.astimezone(LISBON):%d/%m}."),
            "last_fetched_at": last.isoformat(),
            "next_allowed_at": nxt.isoformat(),
        }

    make_slug, model_slug = m["make_slug"], m["model_slug"]
    cars: dict[str, dict] = {}
    pages, total, error = 0, None, None
    segments: dict = {}
    makes: list = []
    names = None
    page, last_page = 1, max_pages
    while page <= last_page:
        url = SV.search_url(make_slug, model_slug, page)
        try:
            resp = _get(url)
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
        pages += 1
        payload = SV.search_payload(resp.text) if resp.status_code == 200 else None
        if payload is None:
            error = f"HTTP {resp.status_code}: página sem resultados legíveis ({url})"
            break
        applied = SV.applied(payload)
        if (applied.get("filter_enum_make", (None,))[0] != make_slug
                or applied.get("filter_enum_model", (None,))[0] != model_slug):
            error = "o Standvirtual não aplicou o filtro de marca e modelo"
            break
        if _relaxed(payload):
            break  # zero results; the site padded the page with looser matches
        if page == 1:
            total = SV.total_count(payload)
            last_page = min(SV.total_pages(payload), max_pages)
            segments = SV.segment_mix(payload)
            makes = SV.parse_catalog(payload)
            names = (applied["filter_enum_make"][1], applied["filter_enum_model"][1])
        for car in SV.parse_search(payload, make_slug, model_slug):
            cars.setdefault(car["external_id"], car)
        page += 1

    if pages == 0 or (error and not cars):
        return {"error": error or "nada lido", "pages": pages, "model_id": model_id}

    new = changed = 0
    for car in cars.values():
        _, inserted, prev = _store(conn, car, model_id)
        new += inserted
        price = car.get("price_eur")
        changed += prev is not None and price is not None and round(prev, 2) != round(price, 2)

    conn.execute("UPDATE car_models SET last_fetched_at = now() WHERE id = %s", (model_id,))
    if names and all(names):
        # Names the site uses, in case follow() had to make them up from slugs.
        conn.execute(
            """UPDATE car_models
                  SET label = CASE WHEN label = make || ' ' || model
                                   THEN %(make)s || ' ' || %(model)s ELSE label END,
                      make = %(make)s, model = %(model)s
                WHERE id = %(id)s AND (make, model) IS DISTINCT FROM (%(make)s, %(model)s)
                  AND NOT EXISTS (SELECT 1 FROM car_models o
                                   WHERE o.make = %(make)s AND o.model = %(model)s AND o.id <> %(id)s)""",
            {"make": names[0], "model": names[1], "id": model_id},
        )
    if segments:
        _put_setting(conn, SEGMENTS_KEY, {str(model_id): segments}, merge=True)
    if makes:
        _save_catalog(conn, makes)
    conn.commit()

    out = {
        "model_id": model_id,
        "label": m["label"],
        "pages": pages,
        "cars": len(cars),
        "new": new,
        "price_changes": changed,
        "total_on_site": total,
        "segments": segments,
    }
    if error:
        out["error"] = error
    return out


def _relaxed(payload: dict) -> bool:
    return bool(((payload.get("advertSearch") or {}).get("relaxation") or {}).get("applied"))


def add_from_url(url: str, user_id: int | None = None) -> int:
    """Read one advert page and store the car. Returns cars.id.

    The car is linked to a followed model when its make and model match one.
    Raises ValueError for a link that is not a Standvirtual car advert or a
    page that cannot be read, LookupError when the advert is gone."""
    url = SV.advert_url(url)
    with connect() as conn:
        recent = conn.execute(
            """SELECT id FROM cars
                WHERE source = %s AND url = %s AND image_urls IS NOT NULL
                  AND last_seen_at > now() - %s""",
            (SV.name, url, DOUBLE_CLICK),
        ).fetchone()
    if recent:
        return recent["id"]

    resp = _get(url)
    if resp.status_code in (404, 410):
        raise LookupError("O anúncio já não existe no Standvirtual.")
    ad = SV.advert_payload(resp.text) if resp.status_code == 200 else None
    if ad is None:
        raise ValueError(f"Não foi possível ler o anúncio (HTTP {resp.status_code}).")
    if (ad.get("status") or "ACTIVE") != "ACTIVE":
        raise LookupError("O anúncio já não está ativo no Standvirtual.")

    car = SV.parse_advert(ad)
    car["url"] = car.get("url") or url
    make_slug, model_slug = SV.advert_slugs(ad)
    with connect() as conn:
        model = conn.execute(
            """SELECT id FROM car_models
                WHERE active
                  AND ((make_slug = %(ms)s AND model_slug = %(mds)s)
                       OR (lower(make) = lower(%(mk)s) AND lower(model) = lower(%(md)s)))
                ORDER BY (make_slug = %(ms)s AND model_slug = %(mds)s) DESC NULLS LAST
                LIMIT 1""",
            {"ms": make_slug, "mds": model_slug, "mk": car.get("make"), "md": car.get("model")},
        ).fetchone()
        car_id, _, _ = _store(conn, car, model["id"] if model else None)
        conn.commit()
    log.info("car %s added from %s (user %s)", car_id, url, user_id)
    return car_id


# -- depreciation --------------------------------------------------------------
#
# Two fits on the same points (median asking price per whole year of age, ages
# with n>=3 only, each weighted by its n):
#
# 1. log P = log P0 - k·age, the exponential. Its yearly loss is the same share
#    every year, 1 - exp(-k), so it can't say when the loss "falls under" 10%:
#    it is either always under or never. It stays as the headline number
#    because one k is easy to compare between models (segment_k), carcost.py
#    uses it for the value at year 5, and it is stable with few points.
#
# 2. log P = a + b·age + c·age², for the sweet spot. The drop from age t to t+1
#    is 1 - exp(b + c·(2t+1)), so with c > 0 the yearly loss shrinks steadily
#    with age: steep at first, flatter later, which is how used-car prices
#    behave. Three parameters, closed-form weighted least squares, nothing to
#    tune. A piecewise fit (two slopes and a knee) says much the same with a
#    knee that jumps whenever one age bin changes, and needs a search over the
#    knee with the 5 to 15 bins a model usually has. A parabola does turn back
#    up past its vertex, so the sweet spot is only looked for inside the ages
#    we have data for, and the curve is only returned over that range.
#    When the fit comes out with c < 0 (loss speeding up with age: noise, or a
#    generation change, not a flattening) the curve falls back to the
#    exponential, whose constant drop is then the answer for every age.
#
# Only ages 0 to MAX_FIT_AGE enter either fit. Both are cross-sectional:
# today's prices of cars of different ages, not what one car loses over time.

def _points(cars: list[tuple[float, float]]) -> tuple[list[dict], list[dict]]:
    bins: dict[int, list[float]] = defaultdict(list)
    for age, price in cars:
        age_bin = max(0, math.floor(age + 0.5))
        if age_bin <= MAX_FIT_AGE:
            bins[age_bin].append(price)
    pts, thin = [], []
    for age in sorted(bins):
        p = {"age": age, "median_price": round(median(bins[age])), "n": len(bins[age])}
        (pts if p["n"] >= MIN_PER_AGE else thin).append(p)
    return pts, thin


def _fit_exp(pts: list[dict]) -> tuple[float, float] | None:
    """Weighted least squares of log price on age. Returns (P0, k)."""
    if len({p["age"] for p in pts}) < 2:
        return None
    w = [p["n"] for p in pts]
    x = [p["age"] for p in pts]
    y = [math.log(p["median_price"]) for p in pts]
    sw = sum(w)
    xm = sum(wi * xi for wi, xi in zip(w, x)) / sw
    ym = sum(wi * yi for wi, yi in zip(w, y)) / sw
    sxx = sum(wi * (xi - xm) ** 2 for wi, xi in zip(w, x))
    sxy = sum(wi * (xi - xm) * (yi - ym) for wi, xi, yi in zip(w, x, y))
    slope = sxy / sxx
    return math.exp(ym - slope * xm), -slope


def _fit_quad(pts: list[dict]) -> tuple[float, float, float] | None:
    """Weighted least squares of log price on age and age². Returns (a, b, c)."""
    if len({p["age"] for p in pts}) < 3:
        return None
    s = [0.0] * 5
    t = [0.0] * 3
    for p in pts:
        w, x, y = p["n"], float(p["age"]), math.log(p["median_price"])
        for i in range(5):
            s[i] += w * x ** i
        for i in range(3):
            t[i] += w * x ** i * y
    a = [[s[0], s[1], s[2], t[0]], [s[1], s[2], s[3], t[1]], [s[2], s[3], s[4], t[2]]]
    for col in range(3):                      # Gaussian elimination, partial pivoting
        piv = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            return None
        a[col], a[piv] = a[piv], a[col]
        for r in range(3):
            if r != col:
                f = a[r][col] / a[col][col]
                a[r] = [rv - f * cv for rv, cv in zip(a[r], a[col])]
    return tuple(a[i][3] / a[i][i] for i in range(3))


def _curve(pts: list[dict], exp_fit: tuple[float, float], quad, drop: float):
    lo, hi = pts[0]["age"], pts[-1]["age"]
    if quad:
        a, b, c = quad
        # Past the vertex the parabola climbs again; a used car does not gain
        # value with age, so the curve holds flat there (yearly drop 0).
        vertex = -b / (2 * c) if c > 0 else math.inf

        def f(age):
            age = min(age, vertex)
            return a + b * age + c * age * age
    else:
        p0, k = exp_fit

        def f(age):
            return math.log(p0) - k * age
    curve, sweet = [], None
    for age in range(lo, hi + 1):
        yearly = 1 - math.exp(f(age + 1) - f(age)) if age < hi else None
        if yearly is not None and sweet is None and yearly < drop:
            sweet = age
        curve.append({"age": age, "price": round(math.exp(f(age))),
                      "yearly_drop": round(yearly, 3) if yearly is not None else None})
    return curve, sweet


def _main_body(cars: list[dict], mix: dict | None) -> str | None:
    """A model's segment: the site's own mix for the model when we have it
    (it counts every advert), else the most common body type among our cars."""
    if mix:
        return max(mix.items(), key=lambda kv: kv[1])[0]
    known = Counter(c["body_type"] for c in cars if c["body_type"])
    return known.most_common(1)[0][0] if known else None


def depreciation(model_id: int, drop: float = SWEET_SPOT_DROP) -> dict:
    """Depreciation curve of one model from the latest price of each of its cars.

    {"enough": False, ...} with fewer than 8 priced cars. Otherwise points
    (median price and n per age, n>=3), thin (ages with n<3, left out of the
    fits), the exponential fit p0 and k with retention_3y and retention_5y,
    the quadratic curve over the observed ages with each year's drop,
    sweet_spot_age (first age whose drop to the next year is under `drop`),
    body_type and segment_k, the mean k of the followed models in the same
    segment (the dashed reference line)."""
    with connect() as conn:
        models = {r["id"]: r for r in conn.execute(
            "SELECT id, label, active FROM car_models").fetchall()}
        rows = conn.execute(
            """SELECT model_id, age_years, price_eur, body_type FROM v_car_current
                WHERE model_id IS NOT NULL AND price_eur IS NOT NULL AND age_years IS NOT NULL"""
        ).fetchall()
        mixes = _setting(conn, SEGMENTS_KEY) or {}

    by_model: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        # Same rounding as _points, so the n>=8 rule counts the cars the fit uses.
        if math.floor(float(r["age_years"]) + 0.5) <= MAX_FIT_AGE:
            by_model[r["model_id"]].append(r)
    own = by_model.get(model_id, [])
    if model_id not in models or len(own) < MIN_CARS:
        return {"enough": False, "model_id": model_id, "n": len(own), "min_n": MIN_CARS,
                "max_age": MAX_FIT_AGE}

    def fit(cars):
        pts, thin = _points([(float(c["age_years"]), float(c["price_eur"])) for c in cars])
        return pts, thin, _fit_exp(pts)

    pts, thin, exp_fit = fit(own)
    if exp_fit is None:
        return {"enough": False, "model_id": model_id, "n": len(own), "min_n": MIN_CARS,
                "max_age": MAX_FIT_AGE,
                "reason": f"menos de duas idades com {MIN_PER_AGE} ou mais carros"}
    p0, k = exp_fit
    quad = _fit_quad(pts)
    if quad and quad[2] < 0:
        quad = None
    curve, sweet = _curve(pts, exp_fit, quad, drop)

    body = _main_body(own, mixes.get(str(model_id)))
    seg_ks = []
    if body:
        for mid, cars in by_model.items():
            if not models.get(mid, {}).get("active") or len(cars) < MIN_CARS:
                continue
            if _main_body(cars, mixes.get(str(mid))) != body:
                continue
            fitted = fit(cars)[2]
            if fitted:
                seg_ks.append(fitted[1])

    return {
        "enough": True,
        "model_id": model_id,
        "label": models[model_id]["label"],
        "n": len(own),
        "points": pts,
        "thin": thin,
        "p0": round(p0),
        "k": round(k, 4),
        "retention_3y": round(math.exp(-3 * k), 3),
        "retention_5y": round(math.exp(-5 * k), 3),
        "quad": {"a": round(quad[0], 5), "b": round(quad[1], 5), "c": round(quad[2], 6)} if quad else None,
        "curve_shape": "quadratica" if quad else "exponencial",
        "max_age": MAX_FIT_AGE,
        "curve": curve,
        "sweet_spot_age": sweet,
        "sweet_spot_drop": drop,
        "body_type": body,
        "segment_k": round(sum(seg_ks) / len(seg_ks), 4) if seg_ks else None,
        "segment_models": len(seg_ks),
    }
