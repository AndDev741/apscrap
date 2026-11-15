-- Read models for the web app and the agent. Applied after 002 and 003, which
-- drop and recreate the views these depend on, so everything here is created
-- again on every migrate.

-- v_deals takes a couple of seconds (latest snapshot and price path for every
-- advert). The app filters it on every request, so it reads this copy instead,
-- refreshed by the nightly job after parsing (apscrap/market.py).
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_deals AS SELECT * FROM v_deals;
CREATE UNIQUE INDEX IF NOT EXISTS mv_deals_id_idx ON mv_deals (id);
CREATE INDEX IF NOT EXISTS mv_deals_zone_idx ON mv_deals (transaction_type, council, parish);
CREATE INDEX IF NOT EXISTS mv_deals_root_idx ON mv_deals (root_id);

-- Shortlist with both votes, for the agent and ad-hoc SQL. The app builds its
-- own richer version.
CREATE OR REPLACE VIEW v_shortlist AS
SELECT s.kind, s.item_id, s.added_at, u.name AS added_by,
       (SELECT v.vote FROM votes v JOIN app_users ua ON ua.id = v.user_id
         WHERE ua.slot = 'a' AND v.kind = s.kind AND v.item_id = s.item_id) AS vote_a,
       (SELECT v.vote FROM votes v JOIN app_users ub ON ub.id = v.user_id
         WHERE ub.slot = 'b' AND v.kind = s.kind AND v.item_id = s.item_id) AS vote_b,
       (SELECT count(*) FROM notes n WHERE n.kind = s.kind AND n.item_id = s.item_id) AS n_notes
FROM shortlist s
LEFT JOIN app_users u ON u.id = s.added_by
WHERE s.removed_at IS NULL;

-- The agent's role (AG-2) reads views and the aggregate tables, nothing that
-- holds photos, settings or conversations. The role is created by CNPG in
-- be-infra (data/postgres.yaml); locally it usually does not exist.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'apscrap_readonly') THEN
        GRANT USAGE ON SCHEMA public TO apscrap_readonly;
        GRANT SELECT ON v_listing_current, v_deals, v_parish_stats, v_market_daily,
                        v_price_changes, v_listing_history, v_area_centroid, v_shortlist,
                        mv_deals, market_agg_daily, ine_values, notes, votes, shortlist,
                        app_users, car_models, commute_destinations
            TO apscrap_readonly;
        IF to_regclass('v_car_current') IS NOT NULL THEN
            GRANT SELECT ON v_car_current TO apscrap_readonly;
        END IF;
        IF to_regclass('v_car_deals') IS NOT NULL THEN
            GRANT SELECT ON v_car_deals TO apscrap_readonly;
        END IF;
    END IF;
END $$;
