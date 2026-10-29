#!/usr/bin/env python3
"""Build the apScrap dashboards in Metabase from scratch.

Dashboards as code. Re-running replaces the cards, so a chart definition is
something you edit here and redeploy rather than something you rebuild by
clicking. Needs a Metabase API key with Administrators rights:

    METABASE_API_KEY=mb_xxx python3 scripts/setup_metabase.py
"""
import os
import sys
import time

import httpx

MB = os.getenv("METABASE_URL", "http://localhost:3002")
KEY = os.getenv("METABASE_API_KEY")
DB_NAME = "apScrap"
DB_DETAILS = {
    "host": "db",
    "port": 5432,
    "dbname": os.getenv("PGDATABASE", "apscrap"),
    "user": os.getenv("PGUSER", "apscrap"),
    "password": os.getenv("PGPASSWORD", "apscrap"),
    "ssl": False,
    "tunnel-enabled": False,
}

api = httpx.Client(base_url=MB, headers={"x-api-key": KEY or ""}, timeout=60)


def call(method: str, path: str, **kw):
    r = api.request(method, path, **kw)
    if r.status_code >= 400:
        sys.exit(f"{method} {path} -> {r.status_code}\n{r.text[:800]}")
    return r.json() if r.content else None


# --------------------------------------------------------------------------
# database connection

def ensure_database() -> int:
    existing = call("GET", "/api/database")["data"]
    for db in existing:
        if db["name"] == DB_NAME or db.get("details", {}).get("dbname") == DB_DETAILS["dbname"]:
            print(f"database already connected: {db['name']} (id {db['id']})")
            return db["id"]
    db = call("POST", "/api/database",
              json={"name": DB_NAME, "engine": "postgres", "details": DB_DETAILS})
    print(f"created database connection (id {db['id']})")
    return db["id"]


def wait_for_sync(db_id: int, want: set[str], timeout: int = 180) -> None:
    """Metabase will not let you query a table it has not catalogued yet."""
    call("POST", f"/api/database/{db_id}/sync_schema")
    deadline = time.time() + timeout
    while time.time() < deadline:
        tables = {t["name"] for t in call("GET", f"/api/database/{db_id}/metadata").get("tables", [])}
        missing = want - tables
        if not missing:
            print(f"sync complete, {len(tables)} tables and views visible")
            return
        print(f"  waiting for sync, still missing: {sorted(missing)}")
        time.sleep(5)
    sys.exit(f"sync timed out, missing {sorted(want - tables)}")


# --------------------------------------------------------------------------
# cards

def card(db_id, name, sql, display, viz=None, tags=None, description=None) -> dict:
    return {
        "name": name,
        "description": description,
        "display": display,
        "visualization_settings": viz or {},
        "dataset_query": {
            "type": "native",
            "database": db_id,
            "native": {"query": sql, "template-tags": tags or {}},
        },
    }


def replace_cards(db_id: int, specs: list[dict]) -> dict[str, int]:
    """Archive any card we made before under the same name, then create fresh."""
    wanted = {s["name"] for s in specs}
    for existing in call("GET", "/api/card") or []:
        if existing["name"] in wanted and not existing.get("archived"):
            call("PUT", f"/api/card/{existing['id']}", json={"archived": True})
    ids = {}
    for spec in specs:
        created = call("POST", "/api/card", json=spec)
        ids[spec["name"]] = created["id"]
        print(f"  card: {spec['name']}")
    return ids


def build_dashboard(name: str, description: str, layout: list[dict], params=None) -> int:
    for d in call("GET", "/api/dashboard") or []:
        if d["name"] == name and not d.get("archived"):
            call("PUT", f"/api/dashboard/{d['id']}", json={"archived": True})
    dash = call("POST", "/api/dashboard",
                json={"name": name, "description": description, "parameters": params or []})
    call("PUT", f"/api/dashboard/{dash['id']}",
         json={"dashcards": layout, "parameters": params or []})
    print(f"dashboard: {name} -> {MB}/dashboard/{dash['id']}")
    return dash["id"]


def place(card_id, row, col, size_x, size_y, mappings=None) -> dict:
    return {
        "id": -abs(hash((card_id, row, col))) % 100000 - 1,
        "card_id": card_id,
        "row": row, "col": col, "size_x": size_x, "size_y": size_y,
        "parameter_mappings": mappings or [],
        "visualization_settings": {},
    }


# --------------------------------------------------------------------------
# card definitions

RENT = "WHERE is_active AND is_plausible AND transaction_type = 'rent'"


def market_cards(db_id) -> list[dict]:
    return [
        card(db_id, "Active rentals", f"""
SELECT count(*) AS "Active rentals" FROM v_listing_current {RENT}
""", "scalar"),

        card(db_id, "Median rent", f"""
SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur)::numeric) AS "Median rent"
FROM v_listing_current {RENT}
""", "scalar", {"column_settings": {'["name","Median rent"]': {"suffix": " EUR"}}}),

        card(db_id, "Median EUR per m2", f"""
SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm)::numeric, 2) AS "EUR per m2"
FROM v_listing_current {RENT}
""", "scalar"),

        card(db_id, "Median rent by parish", f"""
SELECT parish AS "Parish",
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur)::numeric) AS "Median rent"
FROM v_listing_current {RENT}
GROUP BY parish HAVING count(*) >= 3
ORDER BY 2 DESC
""", "bar", {"graph.dimensions": ["Parish"], "graph.metrics": ["Median rent"],
             "graph.y_axis.title_text": "EUR / month"},
             description="Parishes with at least three active adverts."),

        card(db_id, "Median EUR per m2 by parish", f"""
SELECT parish AS "Parish",
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm)::numeric, 2) AS "EUR per m2"
FROM v_listing_current {RENT}
GROUP BY parish HAVING count(*) >= 3
ORDER BY 2 DESC
""", "bar", {"graph.dimensions": ["Parish"], "graph.metrics": ["EUR per m2"]},
             description="The number that actually compares neighbourhoods."),

        card(db_id, "Rental map", f"""
SELECT latitude, longitude, price_eur AS "Price", typology AS "Type",
       area_sqm AS "m2", title AS "Title", url AS "Link"
FROM v_listing_current {RENT} AND latitude IS NOT NULL
""", "map", {"map.type": "pin", "map.latitude_column": "latitude",
             "map.longitude_column": "longitude"},
             description="Only listings enriched by a detail crawl carry coordinates."),

        card(db_id, "Supply by typology", f"""
SELECT typology AS "Typology", count(*) AS "Listings"
FROM v_listing_current {RENT}
GROUP BY 1 ORDER BY 1
""", "bar", {"graph.dimensions": ["Typology"], "graph.metrics": ["Listings"]}),

        card(db_id, "Parish comparison table", """
SELECT council AS "Council", parish AS "Parish", typology AS "Type",
       n_listings AS "N", median_price AS "Median rent",
       median_price_per_sqm AS "EUR per m2", median_area_sqm AS "Median m2",
       avg_days_on_market AS "Avg days listed"
FROM v_parish_stats
WHERE transaction_type = 'rent' AND n_listings >= 3
ORDER BY median_price_per_sqm ASC
""", "table", description="Sorted cheapest per square metre first."),
    ]


def watch_cards(db_id) -> list[dict]:
    return [
        card(db_id, "Price drops last 30 days", """
SELECT count(*) AS "Price drops"
FROM v_price_changes
WHERE direction = 'drop' AND changed_at > now() - interval '30 days'
""", "scalar"),

        card(db_id, "Median EUR per m2 over time", """
SELECT day AS "Day", council AS "Council", median_price_per_sqm AS "EUR per m2"
FROM v_market_daily
WHERE transaction_type = 'rent'
ORDER BY 1
""", "line", {"graph.dimensions": ["Day", "Council"], "graph.metrics": ["EUR per m2"]},
             description="Needs a few weeks of nightly crawls before it says anything."),

        card(db_id, "Recent price changes", """
SELECT changed_at::date AS "Date", direction AS "Direction", parish AS "Parish",
       typology AS "Type", area_sqm AS "m2", prev_price AS "Was",
       new_price AS "Now", delta_pct AS "Change pct", url AS "Link"
FROM v_price_changes
ORDER BY changed_at DESC, abs(delta_pct) DESC
LIMIT 200
""", "table", description="The reason a snapshot is written every single day."),

        card(db_id, "How long things sit on the market", f"""
SELECT CASE
         WHEN days_on_market <   7 THEN 'under 1 week'
         WHEN days_on_market <  30 THEN '1 to 4 weeks'
         WHEN days_on_market <  90 THEN '1 to 3 months'
         WHEN days_on_market < 180 THEN '3 to 6 months'
         ELSE 'over 6 months'
       END AS "Time listed",
       count(*) AS "Listings"
FROM v_listing_current {RENT}
GROUP BY 1 ORDER BY min(days_on_market)
""", "bar", {"graph.dimensions": ["Time listed"], "graph.metrics": ["Listings"]},
             description="A long tail here is your negotiating leverage."),

        card(db_id, "New listings per day", """
SELECT first_seen_at::date AS "Day", count(*) AS "New listings"
FROM v_listing_current
WHERE transaction_type = 'rent'
GROUP BY 1 ORDER BY 1
""", "bar", {"graph.dimensions": ["Day"], "graph.metrics": ["New listings"]}),
    ]


# --------------------------------------------------------------------------
# the filterable shortlist

import uuid  # noqa: E402

FILTERS = [
    # (slug, label, metabase param type, template-tag type, default)
    ("transaction", "Rent or sale", "category", "text",   "rent"),
    ("typology",    "Typology",     "category", "text",   None),
    ("parish",      "Parish",       "category", "text",   None),
    ("max_price",   "Max price",    "number/=", "number", None),
    ("min_area",    "Min area m2",  "number/=", "number", None),
]

SHORTLIST_SQL = """
SELECT title AS "Title", typology AS "Type", area_sqm AS "m2",
       price_eur AS "Price", price_per_sqm AS "EUR per m2",
       parish AS "Parish", floor AS "Floor", energy_cert AS "Energy",
       days_on_market AS "Days listed",
       CASE WHEN is_private_owner THEN 'private owner'
            ELSE coalesce(agency_name, 'agency') END AS "Advertiser",
       url AS "Link"
FROM v_listing_current
WHERE is_active AND is_plausible
  AND transaction_type = {{transaction}}
  [[AND typology = {{typology}}]]
  [[AND parish = {{parish}}]]
  [[AND price_eur <= {{max_price}}]]
  [[AND area_sqm >= {{min_area}}]]
ORDER BY price_per_sqm ASC NULLS LAST
LIMIT 500
"""


def shortlist(db_id):
    tags, params, mappings = {}, [], []
    for slug, label, param_type, tag_type, default in FILTERS:
        tag_id, param_id = str(uuid.uuid4()), uuid.uuid4().hex[:8]
        tag = {"id": tag_id, "name": slug, "display-name": label, "type": tag_type}
        if default is not None:
            tag |= {"default": default, "required": True}
        tags[slug] = tag
        params.append({"id": param_id, "name": label, "slug": slug,
                       "type": param_type, "sectionId": "string"}
                      | ({"default": default} if default is not None else {}))
        mappings.append({"parameter_id": param_id,
                         "target": ["variable", ["template-tag", slug]]})

    spec = card(db_id, "Shortlist", SHORTLIST_SQL, "table", tags=tags,
                description="Cheapest per square metre first. Use the filters above.")
    return spec, params, mappings


# --------------------------------------------------------------------------

def main() -> None:
    if not KEY:
        sys.exit("set METABASE_API_KEY (Admin settings -> Authentication -> API keys)")

    db_id = ensure_database()
    wait_for_sync(db_id, {"v_listing_current", "v_parish_stats",
                          "v_price_changes", "v_market_daily"})

    print("\nmarket overview:")
    ids = replace_cards(db_id, market_cards(db_id))
    build_dashboard(
        "Lisboa rental market",
        "Where the money goes. Rebuilt by scripts/setup_metabase.py.",
        [
            place(ids["Active rentals"],            0,  0, 6, 3),
            place(ids["Median rent"],               0,  6, 6, 3),
            place(ids["Median EUR per m2"],         0, 12, 6, 3),
            place(ids["Supply by typology"],        0, 18, 6, 3),
            place(ids["Median EUR per m2 by parish"], 3, 0, 12, 7),
            place(ids["Median rent by parish"],     3, 12, 12, 7),
            place(ids["Rental map"],               10,  0, 12, 8),
            place(ids["Parish comparison table"],  10, 12, 12, 8),
        ],
    )

    print("\nprice watch:")
    ids = replace_cards(db_id, watch_cards(db_id))
    build_dashboard(
        "Price watch",
        "Movement over time. Fills in as the nightly crawl accumulates history.",
        [
            place(ids["Price drops last 30 days"],            0,  0, 6, 3),
            place(ids["How long things sit on the market"],   0,  6, 18, 6),
            place(ids["Median EUR per m2 over time"],         6,  0, 24, 7),
            place(ids["New listings per day"],               13,  0, 12, 6),
            place(ids["Recent price changes"],               13, 12, 12, 6),
        ],
    )

    print("\nshortlist:")
    spec, params, mappings = shortlist(db_id)
    sid = replace_cards(db_id, [spec])["Shortlist"]
    for m in mappings:
        m["card_id"] = sid
    build_dashboard(
        "Shortlist",
        "Filterable browser over every active advert.",
        [place(sid, 0, 0, 24, 14, mappings)],
        params,
    )

    print(f"\ndone. open {MB}")


if __name__ == "__main__":
    main()
