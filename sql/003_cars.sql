-- Cars (CA): views over cars and car_snapshots for the Carros screen.
-- Dropped first so that re-running this file can change the column list.
DROP VIEW IF EXISTS v_car_deals   CASCADE;
DROP VIEW IF EXISTS v_car_current CASCADE;

-- Every car with its latest price.
--   age_years    from first_registration (advert pages have the month); with
--                only the year, the 1st of June is assumed, as carcost.py does.
--   is_active    seen within 10 days of its model's last fetch. Models are
--                fetched only when someone asks, so "seen recently" has to be
--                relative to that fetch, not to today. A car pasted by URL with
--                no followed model counts as active for 10 days.
--   days_listed  since we first saw it, up to today while active, else up to
--                the last time we saw it. Standvirtual's own creation date moves
--                when a dealer bumps the advert, so it is not kept.
CREATE VIEW v_car_current AS
WITH base AS (
    SELECT c.*,
           m.label           AS model_label,
           m.last_fetched_at AS model_fetched_at,
           s.price_eur,
           s.observed_date   AS price_observed_on,
           COALESCE(c.first_registration,
                    CASE WHEN c.year BETWEEN 1900 AND 2100 THEN make_date(c.year, 6, 1) END)
                             AS reg_date,
           c.last_seen_at >= COALESCE(m.last_fetched_at, now()) - interval '10 days'
                             AS is_active
    FROM cars c
    LEFT JOIN car_models m ON m.id = c.model_id
    LEFT JOIN LATERAL (
        SELECT observed_date, price_eur
        FROM car_snapshots
        WHERE car_id = c.id AND price_eur IS NOT NULL
        ORDER BY observed_date DESC
        LIMIT 1
    ) s ON true
)
SELECT
    id, source, external_id, url, model_id, model_label,
    title, make, model, version, year, first_registration, km,
    fuel, gearbox, body_type, engine_cc, power_hp, co2_gkm, seats, doors,
    location, thumbnail_url, image_urls, is_private_seller,
    price_eur, price_observed_on,
    first_seen_at, last_seen_at, model_fetched_at,
    round(GREATEST((now() AT TIME ZONE 'Europe/Lisbon')::date - reg_date, 0) / 365.25, 1)
                                                        AS age_years,
    is_active,
    EXTRACT(day FROM (CASE WHEN is_active THEN now() ELSE last_seen_at END) - first_seen_at)::int
                                                        AS days_listed
FROM base;

-- Active cars rated against similar active cars of the same model.
--   modelo_ano_km  year within ±1 and km within ±20 000, when that gives ≥8 peers
--   modelo_ano     otherwise year within ±2 at any km, again only with ≥8 peers
--   NULL           otherwise: a median of fewer than 8 cars is not shown as a price
-- The car itself is never its own peer. discount_pct is positive when the car
-- asks less than its peers. rating: bom ≥5% below, acima ≥5% above, justo between.
CREATE VIEW v_car_deals AS
WITH act AS (
    SELECT id, model_id, year, km, price_eur, fuel
    FROM v_car_current
    WHERE is_active AND price_eur IS NOT NULL AND model_id IS NOT NULL
),
near AS (
    SELECT a.id, count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p.price_eur)::numeric(12,2) AS med
    FROM act a
    -- Same fuel too: cheap high-mileage hybrids should not set the bar for petrol cars.
    JOIN act p ON p.model_id = a.model_id AND p.id <> a.id
              AND p.fuel IS NOT DISTINCT FROM a.fuel
              AND abs(p.year - a.year) <= 1
              AND abs(p.km - a.km) <= 20000
    GROUP BY a.id
),
wide AS (
    SELECT a.id, count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p.price_eur)::numeric(12,2) AS med
    FROM act a
    JOIN act p ON p.model_id = a.model_id AND p.id <> a.id
              AND p.fuel IS NOT DISTINCT FROM a.fuel
              AND abs(p.year - a.year) <= 2
    GROUP BY a.id
),
peer AS (
    SELECT a.id,
           CASE WHEN near.n >= 8 THEN 'modelo_ano_km'
                WHEN wide.n >= 8 THEN 'modelo_ano' END                AS peer_level,
           CASE WHEN near.n >= 8 THEN near.n
                WHEN wide.n >= 8 THEN wide.n END                      AS peer_n,
           CASE WHEN near.n >= 8 THEN near.med
                WHEN wide.n >= 8 THEN wide.med END                    AS peer_med_price
    FROM act a
    LEFT JOIN near ON near.id = a.id
    LEFT JOIN wide ON wide.id = a.id
)
SELECT
    c.*,
    p.peer_n::int AS peer_n,
    p.peer_med_price,
    p.peer_level,
    round((p.peer_med_price - c.price_eur) / NULLIF(p.peer_med_price, 0) * 100, 1)
                                                        AS discount_pct,
    CASE WHEN p.peer_med_price IS NULL THEN NULL
         WHEN c.price_eur <= p.peer_med_price * 0.95 THEN 'bom'
         WHEN c.price_eur >= p.peer_med_price * 1.05 THEN 'acima'
         ELSE 'justo' END                               AS rating
FROM v_car_current c
LEFT JOIN peer p ON p.id = c.id
WHERE c.is_active;
