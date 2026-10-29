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
