"""Property adverts as the app and the agent see them: search with filters,
full detail of one advert, and the shortlist with both votes.

Search reads mv_deals (active, plausible, not junk, with peer medians), so it
answers in milliseconds. Detail and the shortlist read v_listing_current by
id, because a saved advert may have gone off the market and still matters.
"""
from __future__ import annotations

from .db import connect

SORTS = {
    "recent": "m.first_seen_at DESC, m.id DESC",
    "discount": "m.deal_score DESC NULLS LAST, m.id",
    "price_asc": "m.price_eur ASC, m.id",
    "price_desc": "m.price_eur DESC, m.id",
    "ppm_asc": "m.price_per_sqm ASC, m.id",
    "commute": "commute_sort ASC NULLS LAST, m.id",
    "drops": "m.n_drops DESC NULLS LAST, m.last_drop_on DESC NULLS LAST, m.id",
}

# Minutes from the listing's point to each person's workplace, per mode, as a
# JSON object {"a_transit": 23, "b_car": 31, ...}. Exact coordinates or the
# parish centre only: one figure for a whole council would be noise.
COMMUTE_SQL = """
(SELECT jsonb_object_agg(cd.slot || '_' || cd.mode, cc.minutes)
   FROM commute_destinations cd
   JOIN commute_cells cc
     ON cc.dest_id = cd.id
    AND cc.lat_idx = round({lat} / cd.step_lat)::int
    AND cc.lon_idx = round({lon} / cd.step_lon)::int
  WHERE cd.step_lat IS NOT NULL AND NOT cd.stale AND {prec} IN ('exact', 'parish'))
"""

VOTES_SQL = """
(SELECT jsonb_object_agg(vu.slot, vv.vote) FROM votes vv JOIN app_users vu ON vu.id = vv.user_id
  WHERE vv.kind = 'listing' AND vv.item_id = {id})
"""


def _commute_expr(alias="m"):
    return COMMUTE_SQL.format(lat=f"{alias}.geo_lat", lon=f"{alias}.geo_lon", prec=f"{alias}.geo_precision")


def _where(f: dict, params: dict) -> list[str]:
    w = ["m.transaction_type = %(mode)s"]
    params["mode"] = f.get("mode") or "rent"
    if f.get("councils"):
        w.append("m.council = ANY(%(councils)s)")
        params["councils"] = f["councils"]
    if f.get("council"):
        w.append("m.council = %(council)s")
        params["council"] = f["council"]
    if f.get("parish"):
        w.append("m.parish = %(parish)s")
        params["parish"] = f["parish"]
    if f.get("property_type"):
        w.append("m.property_type = %(ptype)s")
        params["ptype"] = f["property_type"]
    typs = [t for t in (f.get("typologies") or []) if t]
    if typs:
        plain = [t for t in typs if t != "T4+"]
        cond = ["m.typology = ANY(%(typs)s)"] if plain else []
        if "T4+" in typs:
            cond.append("m.typology IN ('T4', 'T5', 'T6')")
        w.append("(" + " OR ".join(cond) + ")")
        params["typs"] = plain
    if f.get("price_max"):
        w.append("m.price_eur <= %(price_max)s")
        params["price_max"] = float(f["price_max"])
    if f.get("price_min"):
        w.append("m.price_eur >= %(price_min)s")
        params["price_min"] = float(f["price_min"])
    if f.get("area_min"):
        w.append("m.area_sqm >= %(area_min)s")
        params["area_min"] = float(f["area_min"])
    if f.get("extras"):
        w.append("m.extras @> %(extras)s::text[]")
        params["extras"] = list(f["extras"])
    if f.get("below"):
        w.append("m.discount_pct > 0")
    if f.get("drops_only"):
        w.append("m.n_drops > 0")
    if f.get("new_days"):
        w.append("m.first_seen_at > now() - make_interval(days => %(new_days)s)")
        params["new_days"] = int(f["new_days"])
    if not f.get("include_temporary", True):
        w.append("NOT m.is_temporary")
    if f.get("ids"):
        w.append("m.id = ANY(%(ids)s)")
        params["ids"] = [int(i) for i in f["ids"]]
    if f.get("max_commute"):
        who = f.get("commute_who") or "both"
        mode = f.get("commute_mode") or "transit"
        params["cmax"], params["cmode"] = int(f["max_commute"]), mode
        slots = ["a", "b"] if who == "both" else [who]
        for s in slots:
            w.append(f"""EXISTS (SELECT 1 FROM commute_destinations d JOIN commute_cells c
                                  ON c.dest_id = d.id
                                 AND c.lat_idx = round(m.geo_lat / d.step_lat)::int
                                 AND c.lon_idx = round(m.geo_lon / d.step_lon)::int
                               WHERE d.slot = '{s}' AND d.mode = %(cmode)s AND NOT d.stale
                                 AND m.geo_precision IN ('exact', 'parish')
                                 AND c.minutes <= %(cmax)s)""")
    return w


def search(f: dict, limit: int = 24, offset: int = 0, conn=None) -> dict:
    params: dict = {}
    where = " AND ".join(_where(f, params))
    sort = SORTS.get(f.get("sort") or "recent", SORTS["recent"])
    params |= {"limit": min(int(limit), 200), "offset": int(offset)}
    commute = _commute_expr()
    sql = f"""
        SELECT m.id, m.url, m.title, m.typology, m.property_type, m.area_sqm, m.price_eur,
               m.price_per_sqm, m.council, m.parish, m.thumbnail_url, m.image_urls, m.extras,
               m.peer_level, m.peer_n, m.peer_med_ppm, m.peer_med_price, m.discount_pct,
               m.price_discount_pct, m.deal_score, m.n_drops, m.max_price, m.first_price,
               m.last_drop_on, m.price_path, m.days_on_market, m.chain_days_on_market,
               m.n_relists, m.root_id, m.first_seen_at, m.listed_at, m.is_temporary,
               m.is_private_owner, m.agency_name, m.geo_lat, m.geo_lon, m.geo_precision,
               m.floor, m.energy_cert, m.build_year, m.deposit_eur,
               {commute} AS commute,
               {VOTES_SQL.format(id='m.id')} AS votes,
               EXISTS (SELECT 1 FROM shortlist s WHERE s.kind = 'listing' AND s.item_id = m.id
                         AND s.removed_at IS NULL) AS saved,
               (SELECT count(*) FROM notes n WHERE n.kind = 'listing' AND n.item_id = m.id) AS n_notes,
               count(*) OVER () AS total,
               (SELECT min(c.minutes) FROM commute_destinations d JOIN commute_cells c
                  ON c.dest_id = d.id AND c.lat_idx = round(m.geo_lat / d.step_lat)::int
                 AND c.lon_idx = round(m.geo_lon / d.step_lon)::int
                WHERE d.mode = 'transit' AND NOT d.stale AND m.geo_precision IN ('exact', 'parish')) AS commute_sort
        FROM mv_deals m
        WHERE {where}
        ORDER BY {sort}
        LIMIT %(limit)s OFFSET %(offset)s
    """
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        if own:
            conn.close()
    total = rows[0]["total"] if rows else 0
    for r in rows:
        r.pop("total", None)
        r.pop("commute_sort", None)
    return {"total": total, "items": rows}


def facets(mode: str, councils: list[str], conn=None) -> dict:
    """Zones and counts for the filter panel."""
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            """SELECT council, parish, count(*) AS n FROM mv_deals
                WHERE transaction_type = %s AND council = ANY(%s)
                GROUP BY 1, 2 ORDER BY 1, 2""", (mode, councils)).fetchall()
        totals = conn.execute(
            "SELECT transaction_type, count(*) AS n FROM mv_deals WHERE council = ANY(%s) GROUP BY 1",
            (councils,)).fetchall()
    finally:
        if own:
            conn.close()
    return {"zones": rows, "totals": {r["transaction_type"]: r["n"] for r in totals}}


def detail(listing_id: int, conn=None) -> dict | None:
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            f"""SELECT l.*, {_commute_expr('l')} AS commute,
                       {VOTES_SQL.format(id='l.id')} AS votes,
                       EXISTS (SELECT 1 FROM shortlist s WHERE s.kind = 'listing'
                                 AND s.item_id = l.id AND s.removed_at IS NULL) AS saved,
                       d.peer_level, d.peer_n, d.peer_med_ppm, d.peer_med_price, d.discount_pct,
                       d.price_discount_pct, d.deal_score
                  FROM v_listing_current l
                  LEFT JOIN mv_deals d ON d.id = l.id
                 WHERE l.id = %s""", (listing_id,)).fetchone()
        if not row:
            return None
        chain = conn.execute(
            """SELECT id, url, first_seen_at, last_seen_at, listed_at, agency_name,
                      (SELECT price_eur FROM listing_snapshots s WHERE s.listing_id = l.id
                        ORDER BY observed_at DESC LIMIT 1) AS last_price
                 FROM listings l
                WHERE COALESCE(relist_root, id) = %s
                ORDER BY first_seen_at""", (row["root_id"],)).fetchall()
        ine = conn.execute(
            """SELECT indicator, period_end, value FROM ine_values
                WHERE council = %s AND parish IS NOT DISTINCT FROM %s AND value IS NOT NULL
                ORDER BY period_end DESC LIMIT 2""", (row["council"], row["parish"])).fetchall()
        notes = notes_for(conn, "listing", listing_id, row["root_id"])
    finally:
        if own:
            conn.close()
    row["chain"] = chain
    row["ine"] = ine
    row["notes"] = notes
    return row


def notes_for(conn, kind: str, item_id: int, root_id: int | None = None) -> list[dict]:
    ids = [item_id] + ([root_id] if root_id and root_id != item_id else [])
    if kind == "listing" and root_id:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM listings WHERE COALESCE(relist_root, id) = %s", (root_id,)).fetchall()] or ids
    notes = conn.execute(
        """SELECT n.id, n.item_id, n.body, n.checklist, n.visited_on, n.created_at, n.updated_at,
                  u.slot, u.name,
                  COALESCE((SELECT jsonb_agg(p.id ORDER BY p.id) FROM photos p WHERE p.note_id = n.id), '[]') AS photos
             FROM notes n LEFT JOIN app_users u ON u.id = n.user_id
            WHERE n.kind = %s AND n.item_id = ANY(%s)
            ORDER BY n.created_at DESC""", (kind, ids)).fetchall()
    return notes


def shortlist(conn=None) -> list[dict]:
    """Saved adverts. A saved advert that was republished shows its newest
    copy, so votes and notes follow the flat across relists."""
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            f"""WITH saved AS (
                    SELECT s.item_id, s.added_at, s.added_by,
                           COALESCE(l.relist_root, l.id) AS root
                      FROM shortlist s JOIN listings l ON l.id = s.item_id
                     WHERE s.kind = 'listing' AND s.removed_at IS NULL),
                newest AS (
                    SELECT DISTINCT ON (sv.root) sv.*, l.id AS current_id
                      FROM saved sv
                      JOIN listings l ON COALESCE(l.relist_root, l.id) = sv.root
                     ORDER BY sv.root, l.last_seen_at DESC)
                SELECT n.item_id AS saved_id, n.added_at, n.added_by, c.*,
                       {_commute_expr('c')} AS commute,
                       (SELECT jsonb_object_agg(u.slot, v.vote) FROM votes v JOIN app_users u ON u.id = v.user_id
                         WHERE v.kind = 'listing' AND v.item_id IN (n.item_id, c.id)) AS votes,
                       d.discount_pct, d.peer_med_ppm, d.peer_level, d.peer_n, d.deal_score,
                       (SELECT count(*) FROM notes x WHERE x.kind = 'listing'
                         AND x.item_id IN (SELECT id FROM listings WHERE COALESCE(relist_root, id) = n.root)) AS n_notes
                  FROM newest n
                  JOIN v_listing_current c ON c.id = n.current_id
                  LEFT JOIN mv_deals d ON d.id = c.id
                 ORDER BY n.added_at DESC""").fetchall()
    finally:
        if own:
            conn.close()
    return rows
