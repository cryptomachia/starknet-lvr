-- LVR Lab — initial TimescaleDB schema
-- Run order: 001_initial.sql
--
-- Required extensions:
--   CREATE EXTENSION IF NOT EXISTS timescaledb;
--
-- Tables:
--   indexer_checkpoint    — per-pipeline last-processed block
--   swap_events           — Ekubo singleton Swapped events
--   position_events       — opened / updated / closed
--   oracle_observations   — Pragma price feed history
--   gauge_rate_adjustments — DeFi Spring rate-change events
--
-- Materialized views:
--   pool_daily            — per-pool daily volume / fees / σ_fee proxies
--   wedge_panel_daily     — joined to oracle, computed wedge

-- ---------- Tables ----------

CREATE TABLE IF NOT EXISTS indexer_checkpoint (
    pipeline    TEXT PRIMARY KEY,
    last_block  BIGINT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS swap_events (
    block_number  BIGINT      NOT NULL,
    block_ts      TIMESTAMPTZ NOT NULL,
    tx_hash       TEXT        NOT NULL,
    log_index     INT         NOT NULL,
    pool_id       TEXT        NOT NULL,
    direction     TEXT        NOT NULL,
    amount0_wei   NUMERIC     NOT NULL,
    amount1_wei   NUMERIC     NOT NULL,
    fee_wei       NUMERIC     NOT NULL,
    price_pre     DOUBLE PRECISION,
    price_post    DOUBLE PRECISION,
    tick_pre      INT,
    tick_post     INT,
    PRIMARY KEY (block_number, log_index)
);
SELECT create_hypertable('swap_events', 'block_ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_swap_pool_ts ON swap_events (pool_id, block_ts DESC);
CREATE INDEX IF NOT EXISTS idx_swap_block ON swap_events (block_number);

CREATE TABLE IF NOT EXISTS position_events (
    block_number  BIGINT      NOT NULL,
    block_ts      TIMESTAMPTZ NOT NULL,
    tx_hash       TEXT        NOT NULL,
    log_index     INT         NOT NULL,
    event_kind    TEXT        NOT NULL CHECK (event_kind IN ('opened','updated','closed')),
    pool_id       TEXT        NOT NULL,
    nft_id        BIGINT,
    owner         TEXT,
    l_delta       DOUBLE PRECISION,
    p_a           DOUBLE PRECISION,
    p_b           DOUBLE PRECISION,
    fees_collected_token0_wei NUMERIC DEFAULT 0,
    fees_collected_token1_wei NUMERIC DEFAULT 0,
    PRIMARY KEY (block_number, log_index)
);
SELECT create_hypertable('position_events', 'block_ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_position_pool ON position_events (pool_id, block_ts DESC);
CREATE INDEX IF NOT EXISTS idx_position_nft ON position_events (nft_id, block_ts DESC);

CREATE TABLE IF NOT EXISTS oracle_observations (
    block_number  BIGINT      NOT NULL,
    block_ts      TIMESTAMPTZ NOT NULL,
    oracle        TEXT        NOT NULL,
    pair_id       BIGINT      NOT NULL,
    price         DOUBLE PRECISION NOT NULL,
    decimals      INT         NOT NULL,
    n_sources     INT         NOT NULL DEFAULT 0,
    PRIMARY KEY (block_number, oracle, pair_id)
);
SELECT create_hypertable('oracle_observations', 'block_ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_oracle_pair ON oracle_observations (pair_id, block_ts DESC);

CREATE TABLE IF NOT EXISTS gauge_rate_adjustments (
    block_number          BIGINT      NOT NULL,
    block_ts              TIMESTAMPTZ NOT NULL,
    distributor           TEXT        NOT NULL,
    new_rate_per_second   DOUBLE PRECISION NOT NULL,
    old_rate_per_second   DOUBLE PRECISION NOT NULL,
    epoch                 INT,
    PRIMARY KEY (block_number, distributor)
);

-- ---------- Materialized views ----------

CREATE MATERIALIZED VIEW IF NOT EXISTS pool_daily
WITH (timescaledb.continuous) AS
SELECT
    pool_id,
    time_bucket('1 day', block_ts) AS day,
    SUM(amount0_wei) AS vol0_wei,
    SUM(amount1_wei) AS vol1_wei,
    SUM(fee_wei)     AS fees_wei,
    COUNT(*)         AS n_swaps,
    MIN(price_post)  AS price_low,
    MAX(price_post)  AS price_high,
    LAST(price_post, block_ts) AS price_close,
    FIRST(price_post, block_ts) AS price_open
FROM swap_events
GROUP BY pool_id, day
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'pool_daily',
    start_offset => INTERVAL '7 days',
    end_offset   => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Daily oracle-aligned reference price (one observation per day per pair)
CREATE MATERIALIZED VIEW IF NOT EXISTS oracle_daily
WITH (timescaledb.continuous) AS
SELECT
    pair_id,
    oracle,
    time_bucket('1 day', block_ts) AS day,
    AVG(price)           AS avg_price,
    LAST(price, block_ts) AS close_price
FROM oracle_observations
GROUP BY pair_id, oracle, day
WITH NO DATA;

-- ---------- Convenience grants ----------
-- The dashboard's read-only role gets SELECT on hypertables + views
-- Production deployment runs:
--   CREATE ROLE lvrlab_dashboard_ro NOLOGIN;
--   GRANT USAGE ON SCHEMA public TO lvrlab_dashboard_ro;
--   GRANT SELECT ON ALL TABLES IN SCHEMA public TO lvrlab_dashboard_ro;
