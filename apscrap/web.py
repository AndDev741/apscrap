"""The two-person app (DT-3): the site in site/ plus a JSON API under /api.

One small process. Cloudflare Access in front decides who gets in (auth.py);
every write records which of the two made it. Reads come from mv_deals and the
aggregate tables, so nothing here is slow enough to need a cache.

    apscrap serve --port 8080
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import logging
import threading
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb

from . import appsettings, auth, carcost, listings, market
from .config import SITE_SRC
from .db import connect

log = logging.getLogger(__name__)

app = FastAPI(title="Casa & Carro", docs_url=None, redoc_url=None, openapi_url=None)


def _json(obj):
    return JSONResponse(jsonable_encoder(obj, custom_encoder={Decimal: float}))


@app.middleware("http")
async def _revalidate_static(request: Request, call_next):
    """The site has no build step and no hashed file names, so the browser
    must check with the server (ETag) before reusing a cached script, or a
    deploy would leave it running yesterday's JavaScript."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(json.JSONDecodeError)
def _bad_json(_request, exc):
    return JSONResponse({"error": f"invalid JSON: {exc}"}, status_code=400)


@app.exception_handler(auth.AuthError)
def _auth_error(_request, exc: auth.AuthError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def current_user(request: Request) -> dict:
    email = auth.email_from(request.headers, request.cookies)
    return auth.user_for(email)


def _users(conn) -> dict:
    rows = conn.execute("SELECT id, slot, name, email FROM app_users ORDER BY slot").fetchall()
    return {r["slot"]: r for r in rows}


def _councils(settings: dict) -> list[str]:
    return settings["property_criteria"].get("councils") or market.DEFAULT_COUNCILS


# -- who and settings -----------------------------------------------------------

@app.get("/api/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    with connect() as conn:
        users = _users(conn)
        settings = appsettings.get(conn=conn)
        updated = conn.execute(
            "SELECT max(finished_at) AS at FROM crawl_runs WHERE status = 'ok'").fetchone()["at"]
        dests = conn.execute(
            """SELECT id, slot, mode, label, lat, lon, loaded_at, stale,
                      (SELECT count(*) FROM commute_cells c WHERE c.dest_id = d.id) AS cells
                 FROM commute_destinations d ORDER BY slot, mode""").fetchall()
    return _json({"user": user, "users": users, "settings": settings, "updated_at": updated,
                  "commute": dests})


@app.put("/api/settings/{key}")
async def put_setting(key: str, request: Request, user=Depends(current_user)):
    allowed = {"property_criteria", "car_criteria", "agent", "telegram", "zone_alert"}
    if key not in allowed:
        raise HTTPException(400, f"unknown settings key {key}")
    value = await request.json()
    if not isinstance(value, dict):
        raise HTTPException(400, "settings must be an object")
    appsettings.put(key, value, user["id"])
    return {"ok": True}


@app.put("/api/users/{slot}")
async def put_user(slot: str, request: Request, user=Depends(current_user)):
    if slot not in ("a", "b"):
        raise HTTPException(400, "slot is a or b")
    body = await request.json()
    name = (body.get("name") or "").strip()[:40]
    email = (body.get("email") or "").strip().lower() or None
    if not name:
        raise HTTPException(400, "name is required")
    with connect() as conn:
        conn.execute(
            """INSERT INTO app_users (slot, name, email) VALUES (%s, %s, %s)
               ON CONFLICT (slot) DO UPDATE SET name = EXCLUDED.name,
                   email = COALESCE(EXCLUDED.email, app_users.email)""", (slot, name, email))
        conn.commit()
    return {"ok": True}


@app.put("/api/workplace/{slot}")
async def put_workplace(slot: str, request: Request, user=Depends(current_user)):
    """Save a workplace. Its grids go stale until scripts/commute_grid.py runs
    again on the laptop and the result is loaded (IM-6)."""
    body = await request.json()
    label = (body.get("label") or "Trabalho").strip()[:40]
    lat, lon = float(body["lat"]), float(body["lon"])
    if not (38.4 < lat < 39.2 and -9.6 < lon < -8.7):
        raise HTTPException(400, "fora da área de Lisboa")
    with connect() as conn:
        for mode in ("transit", "car"):
            conn.execute(
                """INSERT INTO commute_destinations (slot, mode, label, lat, lon, stale)
                   VALUES (%(s)s, %(m)s, %(l)s, %(lat)s, %(lon)s, true)
                   ON CONFLICT (slot, mode) DO UPDATE SET label = EXCLUDED.label,
                       -- a grid loaded before the point was known is taken to match it
                       stale = commute_destinations.stale
                               OR (commute_destinations.lat IS NOT NULL
                                   AND (commute_destinations.lat, commute_destinations.lon)
                                       IS DISTINCT FROM (EXCLUDED.lat, EXCLUDED.lon)),
                       lat = EXCLUDED.lat, lon = EXCLUDED.lon""",
                {"s": slot, "m": mode, "l": label, "lat": lat, "lon": lon})
        conn.commit()
    return {"ok": True}


# -- property dashboard -----------------------------------------------------------

@app.get("/api/overview")
def overview(mode: str = "rent", user=Depends(current_user)):
    settings = appsettings.get()
    councils = _councils(settings)
    with connect() as conn:
        k = conn.execute(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE first_seen_at > now() - interval '7 days' AND NOT is_backlog) AS new7,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm)
                          FILTER (WHERE property_type = 'flat') AS ppm
                 FROM mv_deals WHERE transaction_type = %s AND council = ANY(%s)
                  AND NOT is_midstay""",
            (mode, councils)).fetchone()
        drops = conn.execute(
            """SELECT count(*) AS n,
                      count(*) FILTER (WHERE EXISTS (
                          SELECT 1 FROM shortlist s JOIN listings l2 ON l2.id = s.item_id
                           WHERE s.kind = 'listing' AND s.removed_at IS NULL
                             AND COALESCE(l2.relist_root, l2.id) = d.root_id)) AS saved
                 FROM v_price_changes c JOIN mv_deals d ON d.id = c.listing_id
                WHERE c.direction = 'drop' AND c.changed_at > now() - interval '7 days'
                  AND d.transaction_type = %s AND d.council = ANY(%s) AND NOT d.is_midstay""",
            (mode, councils)).fetchone()
        sl = conn.execute(
            """SELECT count(*) AS saved,
                      count(*) FILTER (WHERE (SELECT count(*) FROM votes v
                                               WHERE v.kind = 'listing' AND v.item_id = s.item_id
                                                 AND v.vote = 'gosto') = 2) AS agree
                 FROM shortlist s WHERE s.kind = 'listing' AND s.removed_at IS NULL""").fetchone()
        saved_ids = {r["item_id"] for r in conn.execute(
            "SELECT item_id FROM shortlist WHERE kind = 'listing' AND removed_at IS NULL").fetchall()}
    return _json({
        "mode": mode, "councils": councils,
        "median_ppm": round(float(k["ppm"]), 2) if k["ppm"] else None,
        "active": k["n"], "new_7d": k["new7"],
        "drops_7d": drops["n"], "drops_saved": drops["saved"],
        "saved": sl["saved"], "agree": sl["agree"],
        "events": market.events(mode, councils, saved_ids),
    })


@app.get("/api/departures")
def departures(mode: str = "rent", user=Depends(current_user)):
    """What left the market lately and how fast, by price band (IM-3)."""
    return _json(market.departures(mode, _councils(appsettings.get())))


@app.get("/api/zones")
def zones(mode: str = "rent", typology: str | None = None, user=Depends(current_user)):
    settings = appsettings.get()
    return _json(market.zones(mode, _councils(settings), typology or None))


def _filters_from(request: Request) -> dict:
    q = request.query_params
    lst = lambda k: [x for x in q.get(k, "").split(",") if x]  # noqa: E731
    return {
        "mode": q.get("mode", "rent"),
        "council": q.get("council") or None,
        "parish": q.get("parish") or None,
        "property_type": q.get("property_type") or None,
        "typologies": lst("typologies"),
        "price_max": q.get("price_max") or None,
        "price_min": q.get("price_min") or None,
        "area_min": q.get("area_min") or None,
        "extras": lst("extras"),
        "below": q.get("below") in ("1", "true"),
        "drops_only": q.get("drops") in ("1", "true"),
        "new_days": q.get("new_days") or None,
        "max_commute": q.get("max_commute") or None,
        "commute_who": q.get("commute_who") or "both",
        "commute_mode": q.get("commute_mode") or "transit",
        "sort": q.get("sort", "recent"),
        "ids": lst("ids"),
        "include_midstay": q.get("midstay") in ("1", "true"),
        "sources": lst("sources"),
    }


@app.get("/api/listings")
def get_listings(request: Request, limit: int = 24, offset: int = 0, user=Depends(current_user)):
    f = _filters_from(request)
    settings = appsettings.get()
    f["councils"] = _councils(settings)
    out = listings.search(f, limit, offset)
    out["facets"] = listings.facets(f["mode"], f["councils"])
    return _json(out)


@app.get("/api/listings/geo")
def listings_geo(request: Request, user=Depends(current_user)):
    """Every matching advert as [id, lat, lon, price, deal_score] for the map."""
    f = _filters_from(request)
    f["councils"] = _councils(appsettings.get())
    params: dict = {}
    where = " AND ".join(listings._where(f, params))
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT m.id, round(m.geo_lat::numeric, 5) AS lat, round(m.geo_lon::numeric, 5) AS lon,
                       m.price_eur AS p, m.deal_score AS s, m.typology AS t, m.geo_precision AS g
                  FROM mv_deals m WHERE {where} AND m.geo_lat IS NOT NULL""", params).fetchall()
    return _json([[r["id"], r["lat"], r["lon"], r["p"], r["s"], r["t"], r["g"]] for r in rows])


@app.get("/api/listing/{listing_id}")
def get_listing(listing_id: int, user=Depends(current_user)):
    row = listings.detail(listing_id)
    if not row:
        raise HTTPException(404, "anúncio não encontrado")
    return _json(row)


@app.get("/api/commute/{dest_id}")
def commute_grid(dest_id: int, user=Depends(current_user)):
    with connect() as conn:
        d = conn.execute("SELECT * FROM commute_destinations WHERE id = %s", (dest_id,)).fetchone()
        if not d:
            raise HTTPException(404, "sem grelha")
        cells = conn.execute(
            "SELECT lat_idx, lon_idx, minutes FROM commute_cells WHERE dest_id = %s AND minutes <= 60",
            (dest_id,)).fetchall()
    return _json({"dest": d, "cells": [[c["lat_idx"], c["lon_idx"], c["minutes"]] for c in cells]})


# -- the two of them: votes, shortlist, notes, photos ------------------------------

KINDS = ("listing", "car")


@app.post("/api/vote")
async def vote(request: Request, user=Depends(current_user)):
    b = await request.json()
    kind, item_id, value = b.get("kind"), int(b["item_id"]), b.get("vote")
    if kind not in KINDS or value not in ("gosto", "talvez", "nao", None):
        raise HTTPException(400, "voto inválido")
    with connect() as conn:
        if value is None:
            conn.execute("DELETE FROM votes WHERE user_id = %s AND kind = %s AND item_id = %s",
                         (user["id"], kind, item_id))
        else:
            conn.execute(
                """INSERT INTO votes (user_id, kind, item_id, vote) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id, kind, item_id) DO UPDATE SET vote = EXCLUDED.vote, updated_at = now()""",
                (user["id"], kind, item_id, value))
            # A vote saves the item: there is no point voting on something
            # neither of you can find again.
            conn.execute(
                """INSERT INTO shortlist (kind, item_id, added_by) VALUES (%s, %s, %s)
                   ON CONFLICT (kind, item_id) DO UPDATE SET removed_at = NULL""",
                (kind, item_id, user["id"]))
        conn.commit()
    return {"ok": True}


@app.post("/api/shortlist")
async def shortlist_set(request: Request, user=Depends(current_user)):
    b = await request.json()
    kind, item_id, saved = b.get("kind"), int(b["item_id"]), bool(b.get("saved"))
    if kind not in KINDS:
        raise HTTPException(400, "tipo inválido")
    with connect() as conn:
        if saved:
            conn.execute(
                """INSERT INTO shortlist (kind, item_id, added_by) VALUES (%s, %s, %s)
                   ON CONFLICT (kind, item_id) DO UPDATE SET removed_at = NULL""",
                (kind, item_id, user["id"]))
        else:
            conn.execute("UPDATE shortlist SET removed_at = now() WHERE kind = %s AND item_id = %s",
                         (kind, item_id))
        conn.commit()
    return {"ok": True}


@app.get("/api/shortlist")
def shortlist_get(user=Depends(current_user)):
    out = {"listings": listings.shortlist(), "cars": []}
    try:
        from . import carsapi
        out["cars"] = carsapi.saved_cars()
    except ImportError:
        pass
    return _json(out)


@app.get("/api/notes")
def notes_get(kind: str, item_id: int, user=Depends(current_user)):
    with connect() as conn:
        root = None
        if kind == "listing":
            r = conn.execute("SELECT COALESCE(relist_root, id) AS root FROM listings WHERE id = %s",
                             (item_id,)).fetchone()
            root = r["root"] if r else None
        return _json(listings.notes_for(conn, kind, item_id, root))


@app.post("/api/notes")
async def notes_post(request: Request, user=Depends(current_user)):
    b = await request.json()
    if b.get("kind") not in KINDS:
        raise HTTPException(400, "tipo inválido")
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO notes (kind, item_id, user_id, body, checklist, visited_on)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (b["kind"], int(b["item_id"]), user["id"], (b.get("body") or "")[:5000],
             Jsonb(b.get("checklist") or {}), b.get("visited_on") or None)).fetchone()
        conn.execute(
            """INSERT INTO shortlist (kind, item_id, added_by) VALUES (%s, %s, %s)
               ON CONFLICT (kind, item_id) DO UPDATE SET removed_at = NULL""",
            (b["kind"], int(b["item_id"]), user["id"]))
        conn.commit()
    return {"id": row["id"]}


@app.put("/api/notes/{note_id}")
async def notes_put(note_id: int, request: Request, user=Depends(current_user)):
    b = await request.json()
    with connect() as conn:
        conn.execute(
            """UPDATE notes SET body = %s, checklist = %s, visited_on = %s, updated_at = now()
                WHERE id = %s""",
            ((b.get("body") or "")[:5000], Jsonb(b.get("checklist") or {}), b.get("visited_on") or None,
             note_id))
        conn.commit()
    return {"ok": True}


@app.delete("/api/notes/{note_id}")
def notes_delete(note_id: int, user=Depends(current_user)):
    with connect() as conn:
        conn.execute("DELETE FROM notes WHERE id = %s", (note_id,))
        conn.commit()
    return {"ok": True}


MAX_PHOTO_BYTES = 15 * 1024 * 1024


@app.post("/api/notes/{note_id}/photos")
async def photo_upload(note_id: int, file: UploadFile = File(...), user=Depends(current_user)):
    from PIL import Image, ImageOps

    raw = await file.read()
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "foto acima de 15 MB")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - anything Pillow cannot read is not a photo
        raise HTTPException(400, f"não é uma imagem: {exc}") from exc

    def jpeg(im, side, quality):
        im = im.copy()
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue(), im.size

    data, size = jpeg(img, 1600, 82)
    thumb, _ = jpeg(img, 420, 75)
    with connect() as conn:
        note = conn.execute("SELECT kind, item_id FROM notes WHERE id = %s", (note_id,)).fetchone()
        if not note:
            raise HTTPException(404, "nota não encontrada")
        row = conn.execute(
            """INSERT INTO photos (note_id, kind, item_id, user_id, content_type, width, height, data, thumb)
               VALUES (%s, %s, %s, %s, 'image/jpeg', %s, %s, %s, %s) RETURNING id""",
            (note_id, note["kind"], note["item_id"], user["id"], size[0], size[1], data, thumb)).fetchone()
        conn.commit()
    return {"id": row["id"]}


@app.get("/api/photos/{photo_id}")
def photo_get(photo_id: int, thumb: bool = False, user=Depends(current_user)):
    with connect() as conn:
        row = conn.execute(
            f"SELECT content_type, {'COALESCE(thumb, data)' if thumb else 'data'} AS body FROM photos WHERE id = %s",
            (photo_id,)).fetchone()
    if not row:
        raise HTTPException(404, "foto não encontrada")
    return Response(bytes(row["body"]), media_type=row["content_type"],
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.delete("/api/photos/{photo_id}")
def photo_delete(photo_id: int, user=Depends(current_user)):
    with connect() as conn:
        conn.execute("DELETE FROM photos WHERE id = %s", (photo_id,))
        conn.commit()
    return {"ok": True}


# -- saved searches (DU-4) ---------------------------------------------------------

@app.get("/api/searches")
def searches_get(user=Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.kind, s.name, s.filters, s.created_at, u.slot, u.name AS owner
                 FROM saved_searches s LEFT JOIN app_users u ON u.id = s.user_id
                ORDER BY s.created_at DESC""").fetchall()
    return _json(rows)


@app.post("/api/searches")
async def searches_post(request: Request, user=Depends(current_user)):
    b = await request.json()
    if b.get("kind") not in KINDS:
        raise HTTPException(400, "tipo inválido")
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO saved_searches (user_id, kind, name, filters) VALUES (%s, %s, %s, %s) RETURNING id",
            (user["id"], b["kind"], (b.get("name") or "Pesquisa")[:80], Jsonb(b.get("filters") or {}))).fetchone()
        conn.commit()
    return {"id": row["id"]}


@app.delete("/api/searches/{search_id}")
def searches_delete(search_id: int, user=Depends(current_user)):
    with connect() as conn:
        conn.execute("DELETE FROM saved_searches WHERE id = %s", (search_id,))
        conn.commit()
    return {"ok": True}


# -- diary (DU-5) --------------------------------------------------------------------

@app.get("/api/diary")
def diary_get(user=Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM diary ORDER BY week_start DESC LIMIT 12").fetchall()
    return _json(rows)


# -- cars ------------------------------------------------------------------------------

def _cars():
    try:
        from . import carsapi
    except ImportError as exc:
        raise HTTPException(503, f"carros ainda não disponíveis: {exc}") from exc
    return carsapi


@app.get("/api/cars/overview")
def cars_overview(body: str | None = None, user=Depends(current_user)):
    return _json(_cars().overview(body))


@app.get("/api/cars/model/{model_id}")
def cars_model(model_id: int, user=Depends(current_user)):
    return _json(_cars().model_detail(model_id))


@app.get("/api/cars")
def cars_list(request: Request, limit: int = 24, offset: int = 0, user=Depends(current_user)):
    return _json(_cars().search(dict(request.query_params), limit, offset))


@app.get("/api/car/{car_id}")
def car_get(car_id: int, user=Depends(current_user)):
    return _json(_cars().detail(car_id))


@app.get("/api/cars/catalog")
def cars_catalog(user=Depends(current_user)):
    from . import cars
    return _json(cars.catalog())


_refreshing: set[int] = set()


@app.post("/api/cars/follow")
async def cars_follow(request: Request, user=Depends(current_user)):
    from . import cars
    b = await request.json()
    model = cars.follow(b["make_slug"], b["model_slug"], user["id"])
    _start_refresh(model["id"], force=False)
    return _json(model)


@app.post("/api/cars/model/{model_id}/refresh")
def cars_refresh(model_id: int, user=Depends(current_user)):
    started = _start_refresh(model_id, force=False)
    return {"started": started}


def _start_refresh(model_id: int, force: bool) -> bool:
    """Fetching a model's adverts takes a few seconds per page (the polite
    delay), so it runs in a thread and the page polls the model."""
    from . import cars
    if model_id in _refreshing:
        return False
    _refreshing.add(model_id)

    def run():
        try:
            log.info("car refresh %s: %s", model_id, cars.refresh(model_id, force=force))
        except Exception:  # noqa: BLE001
            log.exception("car refresh %s failed", model_id)
        finally:
            _refreshing.discard(model_id)

    threading.Thread(target=run, daemon=True).start()
    return True


@app.post("/api/cars/add")
async def cars_add(request: Request, user=Depends(current_user)):
    from . import cars
    b = await request.json()
    url = (b.get("url") or "").strip()
    if not url.startswith("https://www.standvirtual.com/"):
        raise HTTPException(400, "cola o link de um anúncio do Standvirtual")
    car_id = cars.add_from_url(url, user["id"])
    with connect() as conn:
        conn.execute(
            """INSERT INTO shortlist (kind, item_id, added_by) VALUES ('car', %s, %s)
               ON CONFLICT (kind, item_id) DO UPDATE SET removed_at = NULL""", (car_id, user["id"]))
        conn.commit()
    return {"id": car_id}


@app.post("/api/cars/model/{model_id}/unfollow")
def cars_unfollow(model_id: int, user=Depends(current_user)):
    with connect() as conn:
        conn.execute("UPDATE car_models SET active = false WHERE id = %s", (model_id,))
        conn.commit()
    return {"ok": True}


@app.get("/api/carcost")
def car_cost(price: float, fuel: str = "gasolina", body: str = "outro", age: float = 4,
             engine_cc: int | None = None, co2: int | None = None, year: int | None = None,
             user=Depends(current_user)):
    car = {"price_eur": price, "fuel": fuel, "body_type": body, "age_years": age,
           "engine_cc": engine_cc, "co2_gkm": co2, "year": year}
    return _json(carcost.tco(car, appsettings.get()))


# -- the agent (AG) ------------------------------------------------------------------------

@app.post("/api/agent")
async def agent_ask(request: Request, user=Depends(current_user)):
    from . import agent
    b = await request.json()
    question = (b.get("message") or "").strip()
    if not question:
        raise HTTPException(400, "pergunta vazia")
    # The loop makes blocking HTTP and DB calls; keep it off the event loop.
    import anyio
    result = await anyio.to_thread.run_sync(agent.ask, question, b.get("conversation"), user)
    return _json(result)


@app.get("/api/agent/status")
def agent_status(user=Depends(current_user)):
    from . import agent
    return _json(agent.status())


@app.get("/api/agent/conversation/{conv}")
def agent_conversation(conv: str, user=Depends(current_user)):
    from . import agent
    return _json(agent.history(conv))


# -- the site ------------------------------------------------------------------------------

_ASSET = re.compile(r'(src|href)="([a-z]+\.(?:js|css))"')


def _index_html() -> str:
    """index.html with every local script and stylesheet versioned by the
    content of the site's files. Cloudflare caches .js and .css at the edge and
    in the browser, so without this a deploy keeps serving yesterday's code."""
    site = Path(SITE_SRC)
    digest = hashlib.sha1()
    for f in sorted([*site.glob("*.js"), *site.glob("*.css")]):
        digest.update(f.read_bytes())
    version = digest.hexdigest()[:10]
    html = (site / "index.html").read_text()
    return _ASSET.sub(lambda m: f'{m.group(1)}="{m.group(2)}?v={version}"', html)


@app.get("/")
def index(request: Request):
    # The page itself is public to anyone Access lets through; the API checks
    # the user on every call.
    return HTMLResponse(_index_html(), headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=str(SITE_SRC)), name="site")


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    import uvicorn
    if not auth.configured():
        # Health checks and the static shell still answer, so the pod is up;
        # every API call returns 503 until Access is configured.
        log.warning("Cloudflare Access is not configured: the API will answer 503 "
                    "(set CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD)")
    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=True,
                forwarded_allow_ips="*")


_ = (dt, Form)
