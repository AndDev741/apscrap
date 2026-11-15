"""Cars as the app and the agent see them (CA-1 to CA-4).

cars.py owns fetching (on demand only, per Standvirtual's terms) and the
depreciation fit; this module reads v_car_current / v_car_deals and adds the
five-year cost (carcost.py), votes and the shortlist.

Search pages carry no body type, so a car without one takes the most common
segment of its model (app_settings car_segments, written by cars.refresh).
"""
from __future__ import annotations

import statistics

from . import appsettings, carcost, cars
from .db import connect

BODY_CHIPS = {
    "citadino": ("body", ["citadino", "utilitario"]),
    "suv": ("body", ["suv"]),
    "carrinha": ("body", ["carrinha", "monovolume"]),
    "eletrico": ("fuel", ["eletrico"]),
}

SEG_CTE = """
seg AS (
    SELECT m.key::int AS model_id,
           (SELECT e.key FROM jsonb_each_text(m.value) e ORDER BY e.value::int DESC LIMIT 1) AS body
      FROM app_settings s, jsonb_each(s.value) m
     WHERE s.key = 'car_segments'
)"""

SELECT_CARS = f"""
WITH {SEG_CTE}
SELECT d.id, d.url, d.title, d.make, d.model, d.version, d.year, d.km, d.fuel, d.gearbox,
       COALESCE(d.body_type, seg.body) AS body_type, d.engine_cc, d.power_hp, d.co2_gkm, d.seats,
       d.location, d.thumbnail_url, d.image_urls, d.is_private_seller, d.model_id, d.model_label,
       d.price_eur, d.age_years, d.days_listed, d.first_seen_at, d.first_registration,
       d.peer_n, d.peer_med_price, d.peer_level, d.discount_pct, d.rating,
       (SELECT jsonb_object_agg(u.slot, v.vote) FROM votes v JOIN app_users u ON u.id = v.user_id
         WHERE v.kind = 'car' AND v.item_id = d.id) AS votes,
       EXISTS (SELECT 1 FROM shortlist s WHERE s.kind = 'car' AND s.item_id = d.id
                 AND s.removed_at IS NULL) AS saved,
       (SELECT count(*) FROM notes n WHERE n.kind = 'car' AND n.item_id = d.id) AS n_notes,
       count(*) OVER () AS total
  FROM {{source}} d
  LEFT JOIN seg ON seg.model_id = d.model_id
"""

SORTS = {
    "discount": "d.discount_pct DESC NULLS LAST, d.id",
    "price_asc": "d.price_eur ASC, d.id",
    "price_desc": "d.price_eur DESC, d.id",
    "km_asc": "d.km ASC NULLS LAST, d.id",
    "recent": "d.year DESC NULLS LAST, d.km ASC NULLS LAST",
    "new": "d.first_seen_at DESC, d.id DESC",
}


def _where(q: dict, params: dict) -> list[str]:
    w = ["TRUE"]
    chip = q.get("body")
    if chip in BODY_CHIPS:
        col, vals = BODY_CHIPS[chip]
        params["chip"] = vals
        w.append("COALESCE(d.body_type, seg.body) = ANY(%(chip)s)" if col == "body" else "d.fuel = ANY(%(chip)s)")
    if q.get("fuel"):
        params["fuel"] = q["fuel"]
        w.append("d.fuel = %(fuel)s")
    for key, col, op in (("year_min", "d.year", ">="), ("km_max", "d.km", "<="), ("price_max", "d.price_eur", "<=")):
        if q.get(key) not in (None, ""):
            params[key] = float(q[key])
            w.append(f"{col} {op} %({key})s")
    if q.get("automatic") in ("1", "true", True):
        w.append("d.gearbox = 'automatica'")
    if q.get("good") in ("1", "true", True):
        w.append("d.rating = 'bom'")
    if q.get("model_id"):
        params["model_id"] = int(q["model_id"])
        w.append("d.model_id = %(model_id)s")
    if q.get("ids"):
        params["ids"] = [int(i) for i in str(q["ids"]).split(",") if i]
        w.append("d.id = ANY(%(ids)s)")
    return w


def search(q: dict, limit: int = 24, offset: int = 0) -> dict:
    params: dict = {"limit": min(int(limit), 200), "offset": int(offset)}
    where = " AND ".join(_where(q, params))
    sort = SORTS.get(q.get("sort") or "discount", SORTS["discount"])
    sql = SELECT_CARS.format(source="v_car_deals") + f" WHERE {where} ORDER BY {sort} LIMIT %(limit)s OFFSET %(offset)s"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        models = conn.execute(
            "SELECT id, label FROM car_models WHERE active ORDER BY label").fetchall()
    total = rows[0]["total"] if rows else 0
    for r in rows:
        r.pop("total", None)
    return {"total": total, "items": rows, "models": models}


def _k_for(model_id: int | None) -> float | None:
    if not model_id:
        return None
    d = cars.depreciation(model_id)
    return d.get("k") if d.get("enough") else None


def detail(car_id: int) -> dict:
    with connect() as conn:
        # v_car_deals holds only active cars; a saved car may have gone.
        row = conn.execute(
            SELECT_CARS.format(source="(SELECT c.*, dd.peer_n, dd.peer_med_price, dd.peer_level, dd.discount_pct, dd.rating "
                               "FROM v_car_current c LEFT JOIN v_car_deals dd ON dd.id = c.id)") + " WHERE d.id = %s",
            (car_id,)).fetchone()
        if not row:
            return {"error": "carro não encontrado"}
        hist = conn.execute(
            "SELECT observed_date, price_eur FROM car_snapshots WHERE car_id = %s ORDER BY observed_date",
            (car_id,)).fetchall()
        from .listings import notes_for
        notes = notes_for(conn, "car", car_id)
    row.pop("total", None)
    row["history"] = hist
    row["notes"] = notes
    row["tco"] = carcost.tco(row, appsettings.get(), _k_for(row["model_id"]))
    return row


def saved_cars() -> list[dict]:
    with connect() as conn:
        ids = [r["item_id"] for r in conn.execute(
            "SELECT item_id FROM shortlist WHERE kind = 'car' AND removed_at IS NULL ORDER BY added_at DESC").fetchall()]
    if not ids:
        return []
    with connect() as conn:
        rows = conn.execute(
            SELECT_CARS.format(source="(SELECT c.*, dd.peer_n, dd.peer_med_price, dd.peer_level, dd.discount_pct, dd.rating "
                               "FROM v_car_current c LEFT JOIN v_car_deals dd ON dd.id = c.id)") + " WHERE d.id = ANY(%s)",
            (ids,)).fetchall()
    settings = appsettings.get()
    ks: dict = {}
    for r in rows:
        r.pop("total", None)
        if r["model_id"] not in ks:
            ks[r["model_id"]] = _k_for(r["model_id"])
        r["tco"] = carcost.tco(r, settings, ks[r["model_id"]])
    order = {i: n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda r: order.get(r["id"], 0))


def _model_rows(conn) -> list[dict]:
    return conn.execute(
        f"""WITH {SEG_CTE}
            SELECT m.id, m.label, m.make, m.model, m.last_fetched_at, m.followed_at,
                   count(c.id) FILTER (WHERE c.is_active) AS n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY c.price_eur) FILTER (WHERE c.is_active) AS med_price,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY c.age_years) FILTER (WHERE c.is_active) AS med_age,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY c.km) FILTER (WHERE c.is_active) AS med_km,
                   mode() WITHIN GROUP (ORDER BY c.fuel) AS fuel,
                   max(seg.body) AS body
              FROM car_models m
              LEFT JOIN v_car_current c ON c.model_id = m.id
              LEFT JOIN seg ON seg.model_id = m.id
             WHERE m.active
             GROUP BY m.id ORDER BY m.label""").fetchall()


def overview(body: str | None = None) -> dict:
    settings = appsettings.get()
    with connect() as conn:
        models = _model_rows(conn)
    out = []
    for m in models:
        if body in BODY_CHIPS:
            col, vals = BODY_CHIPS[body]
            if (col == "body" and m["body"] not in vals) or (col == "fuel" and m["fuel"] not in vals):
                continue
        dep = cars.depreciation(m["id"])
        typical = {"price_eur": m["med_price"], "age_years": m["med_age"], "fuel": m["fuel"],
                   "body_type": m["body"], "year": None}
        tco = carcost.tco(typical, settings, dep.get("k") if dep.get("enough") else None) if m["med_price"] else None
        out.append({**m, "depreciation": dep, "tco": tco})
    priced = [m for m in out if m["med_price"] and m["n"]]
    total = sum(m["n"] for m in priced)
    wavg = (lambda key: sum(float(m[key]) * m["n"] for m in priced if m[key] is not None) / total) if total else None
    with_curve = [m for m in out if m["depreciation"].get("enough")]
    best = min(with_curve, key=lambda m: m["depreciation"]["k"]) if with_curve else None
    sweet = [m["depreciation"]["sweet_spot_age"] for m in with_curve if m["depreciation"].get("sweet_spot_age")]
    return {
        "models": out,
        "kpi": {
            "avg_price": round(wavg("med_price")) if total else None,
            "listings": total,
            "avg_age": round(wavg("med_age"), 1) if total else None,
            "avg_km": round(wavg("med_km")) if total else None,
            "sweet_spot_age": statistics.median(sweet) if sweet else None,
            "best_model": best["label"] if best else None,
        },
        "criteria": settings["car_criteria"],
    }


def model_detail(model_id: int) -> dict:
    ov = overview()
    m = next((x for x in ov["models"] if x["id"] == model_id), None)
    if not m:
        return {"error": "modelo não seguido"}
    deals = search({"model_id": model_id, "sort": "discount"}, 12)["items"]
    return {**m, "deals": deals}


def models_summary() -> dict:
    ov = overview()
    return {
        "nota": "Só os modelos que vocês seguem; os anúncios são buscados quando carregam no botão.",
        "modelos": [{
            "id": m["id"], "modelo": m["label"], "anuncios_ativos": m["n"],
            "preco_mediano": round(m["med_price"]) if m["med_price"] else None,
            "idade_mediana": round(m["med_age"], 1) if m["med_age"] else None,
            "km_mediano": round(m["med_km"]) if m["med_km"] else None,
            "combustivel_mais_comum": m["fuel"], "segmento": m["body"],
            "mantem_valor_3_anos": m["depreciation"].get("retention_3y"),
            "mantem_valor_5_anos": m["depreciation"].get("retention_5y"),
            "idade_ponto_doce": m["depreciation"].get("sweet_spot_age"),
            "custo_5_anos_carro_tipico": m["tco"]["total"] if m["tco"] else None,
            "custo_mensal": m["tco"]["per_month"] if m["tco"] else None,
            "atualizado": m["last_fetched_at"],
        } for m in ov["models"]],
    }
