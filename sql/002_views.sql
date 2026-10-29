-- Views for the static site and any ad-hoc SQL. Point readers at these, never at
-- the raw tables.

-- Dropped first so that re-running this file can change the column list.
-- CREATE VIEW refuses to reorder or insert columns.
DROP VIEW IF EXISTS v_deals            CASCADE;
DROP VIEW IF EXISTS v_market_daily     CASCADE;
DROP VIEW IF EXISTS v_parish_stats     CASCADE;
DROP VIEW IF EXISTS v_price_changes    CASCADE;
DROP VIEW IF EXISTS v_listing_current  CASCADE;
DROP VIEW IF EXISTS v_listing_history  CASCADE;
DROP VIEW IF EXISTS v_area_centroid    CASCADE;

-- Where a listing is when its detail page has not been fetched yet: the mean
-- position of the geocoded adverts in the same parish, else the same council.
-- Good enough for a commute estimate, flagged so the page can say "approx".
CREATE VIEW v_area_centroid AS
SELECT council, parish, avg(latitude) AS lat, avg(longitude) AS lon, count(*) AS n,
       -- 1 on the council-wide rows, so a listing with no parish can't match them twice
       GROUPING(parish) AS council_level
FROM listings
WHERE latitude IS NOT NULL
GROUP BY GROUPING SETS ((council, parish), (council))
HAVING count(*) >= 3;

-- Price path of every listing, from its snapshots.
CREATE VIEW v_listing_history AS
WITH ordered AS (
    SELECT listing_id, observed_date, price_eur,
           LAG(price_eur) OVER (PARTITION BY listing_id ORDER BY observed_at) AS prev
    FROM listing_snapshots
    WHERE price_eur IS NOT NULL
)
SELECT
    listing_id,
    (array_agg(price_eur ORDER BY observed_date))[1]                    AS first_price,
    max(price_eur)                                                      AS max_price,
    count(*) FILTER (WHERE prev IS NOT NULL AND price_eur < prev)       AS n_drops,
    count(*) FILTER (WHERE prev IS NOT NULL AND price_eur > prev)       AS n_rises,
    max(observed_date) FILTER (WHERE prev IS NOT NULL AND price_eur < prev) AS last_drop_on,
    -- [[date, price], ...] at every change, for the sparkline on the page
    jsonb_agg(jsonb_build_array(observed_date, price_eur) ORDER BY observed_date)
        FILTER (WHERE prev IS NULL OR price_eur IS DISTINCT FROM prev)  AS price_path
FROM ordered
GROUP BY listing_id;

-- Current state of every listing: newest snapshot joined onto its attributes.
CREATE VIEW v_listing_current AS
SELECT
    l.id, l.source, l.external_id, l.url,
    l.transaction_type, l.property_type, l.typology, l.typology_inferred, l.rooms_total,
    l.area_sqm, l.floor, l.district, l.council, l.parish, l.neighborhood,
    l.latitude, l.longitude, l.build_year, l.energy_cert, l.condition,
    l.deposit_eur, l.is_private_owner, l.agency_name, l.thumbnail_url,
    s.price_eur, s.price_per_sqm, s.title,
    l.listed_at, l.first_seen_at, l.last_seen_at,
    s.observed_at                                   AS price_observed_at,
    (l.last_seen_at > now() - interval '3 days')     AS is_active,
    EXTRACT(day FROM now() - COALESCE(l.listed_at, l.first_seen_at))::int
                                                     AS days_on_market,
    -- Advertisers fat-finger things: a sale price posted under rent, an area
    -- with three extra zeros. Medians survive that, counts and averages do not,
    -- so every aggregate below filters on this.
    (
        s.price_eur IS NOT NULL
        AND l.area_sqm BETWEEN 10 AND 3000
        AND CASE l.transaction_type
                WHEN 'rent' THEN s.price_eur BETWEEN 200 AND 20000
                                 AND s.price_per_sqm BETWEEN 2 AND 100
                WHEN 'sale' THEN s.price_eur BETWEEN 20000 AND 30000000
                                 AND s.price_per_sqm BETWEEN 300 AND 30000
                ELSE true
            END
    )                                                AS is_plausible,
    -- Not a home: a room, a shop, a plot, a garage, a caravan, or a sale posted
    -- under rent. These sit at the very bottom of any €/m² ranking and would
    -- otherwise be the first "deals" on the page. Only the start of the title
    -- and a few unambiguous phrases count, because "moradia com garagem" and
    -- "apartamento com 3 quartos" are homes.
    (
        s.title ~* '^\W*((arrenda|aluga)[- ]?se|alugo|arrendo|arrenda|aluga|para (arrendar|alugar))?\s*(um\s+|uma\s+)?(quart[oa]s?|sala|loja|terreno|lote|armaz[eé]m|escrit[oó]rio|garagem|estacionamento|lugar de garagem|consult[oó]rio|roulot+e?|caravana)\M'
        OR s.title ~* '(ponto comercial|trespasse|\mroulot|\mquart[oa]s? (para|em) (solteir|estudante|alugar|arrendar|rapariga|rapaz|senhora|menina)|^\W*vende-se)'
    )                                                AS is_junk,
    -- Student lets and short stays. Shown with a badge, not hidden: some are
    -- fine for a year, but the price is not comparable with a normal lease.
    s.title ~* '(estudante|temporad|f[ée]rias|curta dura|ano letivo|(setembro|outubro) a (junho|julho)|at[ée] (junho|julho|agosto)|\msemanal|por semana)'
                                                     AS is_temporary,
    COALESCE(l.latitude,  cp.lat, cc.lat)            AS geo_lat,
    COALESCE(l.longitude, cp.lon, cc.lon)            AS geo_lon,
    CASE WHEN l.latitude IS NOT NULL THEN 'exact'
         WHEN cp.lat IS NOT NULL THEN 'parish'
         WHEN cc.lat IS NOT NULL THEN 'council'
    END                                              AS geo_precision,
    cm.minutes                                       AS commute_min,
    h.first_price, h.max_price, h.n_drops, h.n_rises, h.last_drop_on, h.price_path
FROM listings l
JOIN LATERAL (
    SELECT price_eur, price_per_sqm, title, observed_at
    FROM listing_snapshots
    WHERE listing_id = l.id
    ORDER BY observed_at DESC
    LIMIT 1
) s ON true
LEFT JOIN v_area_centroid cp ON cp.council = l.council AND cp.parish = l.parish AND cp.council_level = 0
LEFT JOIN v_area_centroid cc ON cc.council = l.council AND cc.council_level = 1
LEFT JOIN v_listing_history h ON h.listing_id = l.id
LEFT JOIN commute_meta m ON true
-- Commute from exact coordinates or the parish centre, never the council
-- centre: Lisboa alone spans 15 to 60 minutes, so one figure for all of it
-- would be noise dressed as data.
LEFT JOIN commute_cells cm
       ON cm.lat_idx = round(COALESCE(l.latitude,  cp.lat) / m.step_lat)::int
      AND cm.lon_idx = round(COALESCE(l.longitude, cp.lon) / m.step_lon)::int;

-- How a rental compares with its neighbours. The peer group is the narrowest
-- one with at least 8 listings: same parish, type and typology; else same
-- council, type and typology; else same council and type. Thin cells are noise
-- (LIMITATIONS.md), so a parish with three T2s never sets the bar.
CREATE VIEW v_deals AS
WITH base AS (
    SELECT * FROM v_listing_current
    WHERE transaction_type = 'rent' AND is_active AND is_plausible AND NOT is_junk
),
by_parish AS (
    SELECT council, parish, property_type, typology, count(*) AS n,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_sqm) AS med_ppm,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_eur)     AS med_price
    FROM base GROUP BY 1, 2, 3, 4
),
by_council AS (
    SELECT council, property_type, typology, count(*) AS n,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_sqm) AS med_ppm,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_eur)     AS med_price
    FROM base GROUP BY 1, 2, 3
),
by_council_any AS (
    SELECT council, property_type, count(*) AS n,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_sqm) AS med_ppm,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_eur)     AS med_price
    FROM base GROUP BY 1, 2
),
peer AS (
    SELECT b.id,
           CASE WHEN p.n >= 8 THEN 'parish'  WHEN c.n >= 8 THEN 'council'
                WHEN a.n >= 8 THEN 'council_any' END                      AS peer_level,
           CASE WHEN p.n >= 8 THEN p.n       WHEN c.n >= 8 THEN c.n
                WHEN a.n >= 8 THEN a.n END                                AS peer_n,
           CASE WHEN p.n >= 8 THEN p.med_ppm WHEN c.n >= 8 THEN c.med_ppm
                WHEN a.n >= 8 THEN a.med_ppm END                          AS peer_med_ppm,
           CASE WHEN p.n >= 8 THEN p.med_price WHEN c.n >= 8 THEN c.med_price
                WHEN a.n >= 8 THEN a.med_price END                        AS peer_med_price
    FROM base b
    LEFT JOIN by_parish p      ON (p.council, p.parish, p.property_type, p.typology)
                                IS NOT DISTINCT FROM (b.council, b.parish, b.property_type, b.typology)
    LEFT JOIN by_council c     ON (c.council, c.property_type, c.typology)
                                IS NOT DISTINCT FROM (b.council, b.property_type, b.typology)
    LEFT JOIN by_council_any a ON (a.council, a.property_type)
                                IS NOT DISTINCT FROM (b.council, b.property_type)
)
SELECT b.*, p.peer_level, p.peer_n,
       ROUND(p.peer_med_ppm::numeric, 2)                                   AS peer_med_ppm,
       ROUND(p.peer_med_price::numeric, 0)                                 AS peer_med_price,
       -- positive = cheaper than the neighbours per m²
       ROUND((1 - b.price_per_sqm / NULLIF(p.peer_med_ppm, 0))::numeric * 100, 1) AS discount_pct,
       ROUND((1 - b.price_eur / NULLIF(p.peer_med_price, 0))::numeric * 100, 1)   AS price_discount_pct,
       -- What the page sorts by. €/m² alone rewards an area typo (a T1 "with
       -- 132 m²"), the headline price alone rewards a tiny flat, so it takes
       -- the mean of the two.
       ROUND(((1 - b.price_per_sqm / NULLIF(p.peer_med_ppm, 0))
            + (1 - b.price_eur / NULLIF(p.peer_med_price, 0)))::numeric * 50, 1)  AS deal_score
FROM base b
JOIN peer p ON p.id = b.id;

-- Every price move, with direction and size. The reason we store snapshots.
CREATE VIEW v_price_changes AS
WITH ordered AS (
    SELECT
        s.listing_id, s.observed_at, s.price_eur,
        LAG(s.price_eur)   OVER w AS prev_price,
        LAG(s.observed_at) OVER w AS prev_observed_at
    FROM listing_snapshots s
    WINDOW w AS (PARTITION BY s.listing_id ORDER BY s.observed_at)
)
SELECT
    o.listing_id, l.url, l.council, l.parish, l.typology, l.area_sqm,
    o.prev_observed_at, o.observed_at AS changed_at,
    o.prev_price, o.price_eur AS new_price,
    (o.price_eur - o.prev_price)                                   AS delta_eur,
    ROUND((o.price_eur - o.prev_price) / NULLIF(o.prev_price, 0) * 100, 2) AS delta_pct,
    CASE WHEN o.price_eur < o.prev_price THEN 'drop' ELSE 'rise' END AS direction
FROM ordered o
JOIN listings l ON l.id = o.listing_id
WHERE o.prev_price IS NOT NULL
  AND o.price_eur IS DISTINCT FROM o.prev_price;

-- The core comparison table: what does a T2 cost where.
CREATE VIEW v_parish_stats AS
SELECT
    council, parish, transaction_type, property_type, typology,
    COUNT(*)                                                             AS n_listings,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_eur)::numeric, 0)     AS median_price,
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price_eur)::numeric, 0)    AS p25_price,
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price_eur)::numeric, 0)    AS p75_price,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_sqm)::numeric, 2) AS median_price_per_sqm,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY area_sqm)::numeric, 0)      AS median_area_sqm,
    ROUND(AVG(days_on_market)::numeric, 0)                                        AS avg_days_on_market
FROM v_listing_current
WHERE is_active AND is_plausible AND NOT is_junk
GROUP BY council, parish, transaction_type, property_type, typology;

-- Market drift over time. Needs a few weeks of history before it says anything.
CREATE VIEW v_market_daily AS
SELECT
    s.observed_date                                                              AS day,
    l.council, l.transaction_type, l.property_type, l.typology,
    COUNT(DISTINCT l.id)                                                         AS n_listings,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.price_eur)::numeric, 0)     AS median_price,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.price_per_sqm)::numeric, 2) AS median_price_per_sqm
FROM listing_snapshots s
JOIN listings l ON l.id = s.listing_id
WHERE s.price_eur IS NOT NULL
  AND l.area_sqm BETWEEN 10 AND 3000
  AND CASE l.transaction_type
          WHEN 'rent' THEN s.price_eur BETWEEN 200 AND 20000
          WHEN 'sale' THEN s.price_eur BETWEEN 20000 AND 30000000
          ELSE true
      END
GROUP BY 1, 2, 3, 4, 5;
