-- apScrap schema
-- Layer 1 (raw_pages) keeps every payload we ever fetched, untouched.
-- Layer 2 (listings + listing_snapshots) is the normalised, queryable form.
-- raw_pages doubles as the work queue: parsed_at IS NULL means "not yet processed".

CREATE TABLE IF NOT EXISTS crawl_runs (
    id           bigserial PRIMARY KEY,
    source       text        NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    pages_fetched int        NOT NULL DEFAULT 0,
    items_seen    int        NOT NULL DEFAULT 0,
    status       text        NOT NULL DEFAULT 'running',  -- running | ok | failed
    error        text,
    params       jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS raw_pages (
    id           bigserial PRIMARY KEY,
    run_id       bigint      REFERENCES crawl_runs(id) ON DELETE SET NULL,
    source       text        NOT NULL,          -- 'imovirtual'
    kind         text        NOT NULL,          -- 'search' | 'detail'
    url          text        NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    http_status  int         NOT NULL,
    payload      jsonb,                         -- extracted __NEXT_DATA__ subtree
    parsed_at    timestamptz,
    parse_error  text
);

-- The queue index: consumers scan only unparsed rows.
CREATE INDEX IF NOT EXISTS raw_pages_unparsed_idx
    ON raw_pages (id) WHERE parsed_at IS NULL;
CREATE INDEX IF NOT EXISTS raw_pages_fetched_idx ON raw_pages (source, kind, fetched_at DESC);

-- One row per real-world advert. Slowly-changing attributes live here.
CREATE TABLE IF NOT EXISTS listings (
    id                bigserial PRIMARY KEY,
    source            text NOT NULL,
    external_id       text NOT NULL,
    url               text NOT NULL,
    transaction_type  text,               -- rent | sale
    property_type     text,               -- flat | house | ...
    typology          text,               -- T0..T6, parsed from the Portuguese title
    typology_inferred boolean DEFAULT false, -- true => derived from room count, less reliable
    rooms_total       int,                -- portal's own count (living room included)
    area_sqm          numeric(10,2),
    floor             text,
    district          text,
    council           text,
    parish            text,
    neighborhood      text,
    street            text,
    latitude          double precision,
    longitude         double precision,
    build_year        int,
    energy_cert       text,
    condition         text,
    deposit_eur       numeric(12,2),
    is_private_owner  boolean,
    agency_name       text,
    listed_at         timestamptz,        -- when the portal first published it
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now(),
    detail_fetched_at timestamptz,        -- null => detail page not yet crawled
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS listings_parish_idx   ON listings (council, parish);
CREATE INDEX IF NOT EXISTS listings_lastseen_idx ON listings (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS listings_needdetail_idx
    ON listings (id) WHERE detail_fetched_at IS NULL;

-- One row per observation. This is what makes price history possible.
CREATE TABLE IF NOT EXISTS listing_snapshots (
    id             bigserial PRIMARY KEY,
    listing_id     bigint      NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    observed_at    timestamptz NOT NULL DEFAULT now(),
    observed_date  date        NOT NULL DEFAULT CURRENT_DATE,
    price_eur      numeric(12,2),
    price_per_sqm  numeric(10,2),
    title          text,
    is_promoted    boolean,
    pushed_up_at   timestamptz,
    raw_page_id    bigint REFERENCES raw_pages(id) ON DELETE SET NULL,
    -- One observation per listing per day. A promoted advert shows up on several
    -- search pages in the same run; without this it would be counted twice.
    UNIQUE (listing_id, observed_date)
);

CREATE INDEX IF NOT EXISTS snapshots_listing_time_idx
    ON listing_snapshots (listing_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS snapshots_time_idx ON listing_snapshots (observed_at DESC);

-- Added 2026-10-29. ADD COLUMN IF NOT EXISTS keeps this file safe to re-run on
-- a database created before the column existed.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS thumbnail_url text;

-- Public-transit minutes from a grid of points to one destination (work),
-- computed offline by scripts/commute_grid.py and loaded with
-- `apscrap commute-load`. A listing finds its cell by rounding its coordinates
-- to the grid step, so the lookup is an equality join and costs nothing.
CREATE TABLE IF NOT EXISTS commute_meta (
    id           int PRIMARY KEY DEFAULT 1 CHECK (id = 1),   -- exactly one row
    label        text        NOT NULL,
    step_lat     double precision NOT NULL,
    step_lon     double precision NOT NULL,
    departure    timestamptz NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commute_cells (
    lat_idx  int NOT NULL,
    lon_idx  int NOT NULL,
    minutes  int NOT NULL,
    PRIMARY KEY (lat_idx, lon_idx)
);

-- What the Telegram alert has already told you about, so a listing is announced
-- once when it appears and once per price drop, never every night.
CREATE TABLE IF NOT EXISTS alerts_sent (
    listing_id  bigint      NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    kind        text        NOT NULL,           -- 'new' | 'drop:<price>'
    sent_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, kind)
);

-- ---------------------------------------------------------------------------
-- Added 2026-11-15 for the two-person app (REQUIREMENTS.md). Everything below
-- is safe to re-run, like the rest of this file.
-- ---------------------------------------------------------------------------

-- Extras (garage, lift, balcony...) and photo URLs come from detail pages.
-- relist_of points at the older advert this one republishes (IM-3).
ALTER TABLE listings ADD COLUMN IF NOT EXISTS extras     text[];
ALTER TABLE listings ADD COLUMN IF NOT EXISTS image_urls text[];
ALTER TABLE listings ADD COLUMN IF NOT EXISTS relist_of  bigint REFERENCES listings(id) ON DELETE SET NULL;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS relist_checked_at timestamptz;
-- Kept up to date by apscrap/relist.py so the views never walk the chain.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS relist_root      bigint;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS relist_count     int;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS chain_first_seen timestamptz;
CREATE INDEX IF NOT EXISTS listings_relist_idx ON listings (relist_of) WHERE relist_of IS NOT NULL;

-- DT-2: one row per day and peer group, written every night from the
-- snapshots. '' in parish means the whole council, '' in typology every
-- typology. Cheap to query for trends, and it survives any pruning.
CREATE TABLE IF NOT EXISTS market_agg_daily (
    day              date    NOT NULL,
    council          text    NOT NULL,
    parish           text    NOT NULL,
    transaction_type text    NOT NULL,
    property_type    text    NOT NULL,
    typology         text    NOT NULL,
    n                int     NOT NULL,
    median_price     numeric(12,2),
    median_ppm       numeric(10,2),
    PRIMARY KEY (day, council, parish, transaction_type, property_type, typology)
);

-- INE's official medians by parish (DT-1). One row per indicator, reference
-- period and geography; council/parish are our names, matched on import.
CREATE TABLE IF NOT EXISTS ine_values (
    indicator   text    NOT NULL,          -- 'sale_ppm' | 'rent_ppm'
    period      text    NOT NULL,          -- INE's period code
    period_end  date    NOT NULL,
    geo_code    text    NOT NULL,
    geo_name    text,
    council     text,
    parish      text,
    value       numeric(10,2),
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (indicator, period, geo_code)
);

-- The two people. slot 'a' and 'b' fix colour and position everywhere.
CREATE TABLE IF NOT EXISTS app_users (
    id          serial PRIMARY KEY,
    slot        text UNIQUE NOT NULL CHECK (slot IN ('a', 'b')),
    email       text UNIQUE,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ST-1: everything personal, as JSON per key. Never in git.
CREATE TABLE IF NOT EXISTS app_settings (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  int REFERENCES app_users(id) ON DELETE SET NULL
);

-- DU-1. kind is 'listing' or 'car'; item_id is listings.id or cars.id.
CREATE TABLE IF NOT EXISTS votes (
    user_id     int    NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    kind        text   NOT NULL CHECK (kind IN ('listing', 'car')),
    item_id     bigint NOT NULL,
    vote        text   NOT NULL CHECK (vote IN ('gosto', 'talvez', 'nao')),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind, item_id)
);

CREATE TABLE IF NOT EXISTS shortlist (
    kind        text   NOT NULL CHECK (kind IN ('listing', 'car')),
    item_id     bigint NOT NULL,
    added_by    int REFERENCES app_users(id) ON DELETE SET NULL,
    added_at    timestamptz NOT NULL DEFAULT now(),
    removed_at  timestamptz,                 -- kept, with its votes and notes
    PRIMARY KEY (kind, item_id)
);

-- DU-3. Photos live in the database so the nightly pg_dump backs them up.
CREATE TABLE IF NOT EXISTS notes (
    id          bigserial PRIMARY KEY,
    kind        text   NOT NULL CHECK (kind IN ('listing', 'car')),
    item_id     bigint NOT NULL,
    user_id     int REFERENCES app_users(id) ON DELETE SET NULL,
    body        text   NOT NULL DEFAULT '',
    checklist   jsonb  NOT NULL DEFAULT '{}'::jsonb,
    visited_on  date,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notes_item_idx ON notes (kind, item_id);

CREATE TABLE IF NOT EXISTS photos (
    id           bigserial PRIMARY KEY,
    note_id      bigint REFERENCES notes(id) ON DELETE CASCADE,
    kind         text   NOT NULL,
    item_id      bigint NOT NULL,
    user_id      int REFERENCES app_users(id) ON DELETE SET NULL,
    content_type text   NOT NULL,
    width        int,
    height       int,
    data         bytea  NOT NULL,
    thumb        bytea,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS photos_item_idx ON photos (kind, item_id);

-- DU-4: saved searches per person, and a log so each event is sent once.
CREATE TABLE IF NOT EXISTS saved_searches (
    id          bigserial PRIMARY KEY,
    user_id     int REFERENCES app_users(id) ON DELETE CASCADE,
    kind        text  NOT NULL CHECK (kind IN ('listing', 'car')),
    name        text  NOT NULL,
    filters     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS notifications_sent (
    key         text PRIMARY KEY,
    sent_at     timestamptz NOT NULL DEFAULT now()
);

-- DU-5: one entry per week. facts come from SQL; prose is optional.
CREATE TABLE IF NOT EXISTS diary (
    week_start  date PRIMARY KEY,
    facts       jsonb NOT NULL,
    prose       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- AG: conversations and what they cost.
CREATE TABLE IF NOT EXISTS agent_messages (
    id            bigserial PRIMARY KEY,
    conversation  uuid  NOT NULL,
    user_id       int REFERENCES app_users(id) ON DELETE SET NULL,
    role          text  NOT NULL,
    content       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_messages_conv_idx ON agent_messages (conversation, id);
CREATE TABLE IF NOT EXISTS agent_usage (
    id                bigserial PRIMARY KEY,
    at                timestamptz NOT NULL DEFAULT now(),
    user_id           int,
    model             text,
    prompt_tokens     int,
    completion_tokens int,
    cost_eur          numeric(10,4) NOT NULL DEFAULT 0
);

-- IM-6: one grid per person and mode. The legacy single grid becomes
-- destination 1 (person a, transit). lat/lon is the workplace as set in the
-- app; stale means the grid in commute_cells no longer matches it.
CREATE TABLE IF NOT EXISTS commute_destinations (
    id          serial PRIMARY KEY,
    slot        text NOT NULL CHECK (slot IN ('a', 'b')),
    mode        text NOT NULL CHECK (mode IN ('transit', 'car')),
    label       text NOT NULL,
    lat         double precision,
    lon         double precision,
    step_lat    double precision,
    step_lon    double precision,
    departure   timestamptz,
    loaded_at   timestamptz,
    stale       boolean NOT NULL DEFAULT false,
    UNIQUE (slot, mode)
);

ALTER TABLE commute_cells ADD COLUMN IF NOT EXISTS dest_id int NOT NULL DEFAULT 1;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'commute_cells_pkey' AND array_length(conkey, 1) = 2) THEN
        ALTER TABLE commute_cells DROP CONSTRAINT commute_cells_pkey;
        ALTER TABLE commute_cells ADD PRIMARY KEY (dest_id, lat_idx, lon_idx);
    END IF;
END $$;

INSERT INTO commute_destinations (id, slot, mode, label, step_lat, step_lon, departure, loaded_at)
SELECT 1, 'a', 'transit', label, step_lat, step_lon, departure, loaded_at
FROM commute_meta
WHERE NOT EXISTS (SELECT 1 FROM commute_destinations);
SELECT setval(pg_get_serial_sequence('commute_destinations', 'id'),
              GREATEST((SELECT max(id) FROM commute_destinations), 1));

-- Cars (CA). Standvirtual's terms forbid repeated, systematic extraction, so
-- nothing here is crawled on a schedule: a model is fetched only when one of
-- the two presses "Seguir modelo" or "Atualizar", and a single advert only
-- when one of them pastes its link.
CREATE TABLE IF NOT EXISTS car_models (
    id               serial PRIMARY KEY,
    make             text NOT NULL,
    model            text NOT NULL,
    label            text NOT NULL,
    make_slug        text,
    model_slug       text,
    followed_by      int REFERENCES app_users(id) ON DELETE SET NULL,
    followed_at      timestamptz NOT NULL DEFAULT now(),
    last_fetched_at  timestamptz,
    active           boolean NOT NULL DEFAULT true,
    UNIQUE (make, model)
);

CREATE TABLE IF NOT EXISTS cars (
    id                 bigserial PRIMARY KEY,
    source             text NOT NULL,
    external_id        text NOT NULL,
    url                text NOT NULL,
    model_id           int REFERENCES car_models(id) ON DELETE SET NULL,
    title              text,
    make               text,
    model              text,
    version            text,
    year               int,
    first_registration date,
    km                 int,
    fuel               text,   -- gasolina | gasoleo | hibrido | hibrido_plugin | eletrico | gpl
    gearbox            text,   -- manual | automatica
    body_type          text,   -- citadino | utilitario | carrinha | suv | monovolume | sedan | coupe | cabrio | outro
    engine_cc          int,
    power_hp           int,
    co2_gkm            int,
    seats              int,
    doors              int,
    location           text,
    thumbnail_url      text,
    image_urls         text[],
    is_private_seller  boolean,
    first_seen_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS cars_model_idx ON cars (model_id);

CREATE TABLE IF NOT EXISTS car_snapshots (
    car_id         bigint NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    observed_date  date   NOT NULL,
    price_eur      numeric(12,2),
    PRIMARY KEY (car_id, observed_date)
);

-- ---------------------------------------------------------------------------
-- Added 2026-11-15: when an advert left, mid-stay platforms, and more portals.
-- ---------------------------------------------------------------------------

-- An advert is active until apscrap/lifecycle.py marks it gone: missing from
-- its portal for longer than that portal's grace period, or flagged rented by
-- the portal itself (ERA, KW). gone_at is when it was last seen live.
--   gone_reason: 'missing'  not in the crawl any more
--                'relisted' missing, and a newer advert republishes it
--                'removed'  its page answers 404/410
--                'rented'   the portal says rented ("Já ERA", isRented)
-- A gone advert that shows up again is active again (gone_at back to NULL).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'listings' AND column_name = 'gone_at') THEN
        ALTER TABLE listings ADD COLUMN gone_at timestamptz;
        ALTER TABLE listings ADD COLUMN gone_reason text;
        -- One-off backfill with the rule the views used until now (not seen
        -- in the three days before the portal's newest observation), so the
        -- site does not flash every vanished advert back as active.
        UPDATE listings l
           SET gone_at = l.last_seen_at, gone_reason = 'missing'
          FROM (SELECT source, max(last_seen_at) AS newest FROM listings GROUP BY source) s
         WHERE s.source = l.source AND l.last_seen_at < s.newest - interval '3 days';
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS listings_live_idx ON listings (source) WHERE gone_at IS NULL;
CREATE INDEX IF NOT EXISTS listings_gone_idx ON listings (gone_at DESC) WHERE gone_at IS NOT NULL;

-- The portal marks it reserved but still shows it (ERA "Reservado", KW).
ALTER TABLE listings ADD COLUMN IF NOT EXISTS is_reserved boolean;
-- When the portal says the advert last changed (RE/MAX sitemap lastmod, KW).
-- A newer value than detail_fetched_at queues the detail page again.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS source_updated_at timestamptz;
-- Free-text description where the portal gives it on the search card or the
-- detail page (Casa Sapo, RE/MAX, ERA, KW). Imovirtual's stays in raw_pages.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS description text;

-- The same flat advertised on another portal (apscrap/dedup.py). dup_of points
-- at the copy the app shows; that one lists the others as "também em".
-- Separate from relist_of, which links reposts on the same portal over time.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS dup_of bigint REFERENCES listings(id) ON DELETE SET NULL;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS dup_checked_at timestamptz;
CREATE INDEX IF NOT EXISTS listings_dup_idx ON listings (dup_of) WHERE dup_of IS NOT NULL;

-- Mid-stay platforms: furnished flats let by the month (1 to 12 months), at
-- about 35 €/m² against 22 for a normal lease, with prices that move by a few
-- euros every day. They are a third of the rentals on Imovirtual and more than
-- half of Lisboa's T1s, so left in they set the medians. The views keep them
-- out of every median and deal score; the app shows them only on request.
-- A function so the views and market_agg_daily agree.
CREATE OR REPLACE FUNCTION is_midstay(agency text, title text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $f$
    SELECT COALESCE(agency ~* '^\s*(uniplaces|spacest|ukio)', false)
        OR COALESCE(title ~* '(m[eé]dia dura[cç][aã]o|mid[- ]?term|mid[- ]?stay)', false)
$f$;

-- Price bounds an advert must fall in to count anywhere. Below them sit
-- placeholders (Uniplaces posts €1 between tenants) and typos.
CREATE OR REPLACE FUNCTION is_plausible_price(transaction_type text, price numeric) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $f$
    SELECT CASE transaction_type
               WHEN 'rent' THEN price BETWEEN 200 AND 20000
               WHEN 'sale' THEN price BETWEEN 20000 AND 30000000
               ELSE price > 0
           END
$f$;

-- A change counts as a price move from 1% for rent and 0.5% for sale. Below
-- that it is a €5 nudge to climb back up the search results, or a mid-stay
-- platform's daily repricing, and says nothing about room to negotiate.
CREATE OR REPLACE FUNCTION is_price_move(transaction_type text, prev numeric, price numeric) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $f$
    SELECT prev > 0 AND price IS NOT NULL
       AND abs(price - prev) / prev >= CASE transaction_type WHEN 'sale' THEN 0.005 ELSE 0.01 END
$f$;

-- For pruning superseded copies of advert pages that RE/MAX and KW re-read
-- (db.prune_raw_search), and for finding an advert's latest page.
CREATE INDEX IF NOT EXISTS raw_pages_url_idx ON raw_pages (source, url) WHERE kind = 'listing';
