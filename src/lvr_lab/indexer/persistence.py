"""
Persistence layer — writes domain events into TimescaleDB hypertables.

Schema (also lives in `db/migrations/001_initial.sql`):

    CREATE TABLE swap_events (
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
    SELECT create_hypertable('swap_events', 'block_ts');
    CREATE INDEX idx_swap_pool ON swap_events (pool_id, block_ts DESC);

    -- Materialized view: per-pool daily fees / volume / σ_fee
    CREATE MATERIALIZED VIEW pool_daily AS
    SELECT
        pool_id,
        time_bucket('1 day', block_ts) AS day,
        sum(amount0_wei) as vol0_wei,
        sum(amount1_wei) as vol1_wei,
        sum(fee_wei)     as fees_wei,
        count(*)         as n_swaps
    FROM swap_events
    GROUP BY pool_id, day;
    SELECT add_continuous_aggregate_policy('pool_daily', '1 day', '5 minutes', '1 hour');

This module presents the Python API that the event handlers call.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from ..domain.events import (
    SwapEvent, PositionOpenedEvent, PositionUpdatedEvent,
    PositionClosedEvent, OracleUpdateEvent, GaugeRateAdjustedEvent,
)


@dataclass
class TimescalePersistence:
    """Wraps the Postgres connection; provides idempotent upsert handlers.

    Idempotency: each event is uniquely identified by (block_number, log_index).
    Upserts use ON CONFLICT DO NOTHING for safety on replay.
    """
    conn: object   # psycopg connection

    def on_swap(self, ev: SwapEvent) -> None:
        s = ev.swap
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO swap_events (
                    block_number, block_ts, tx_hash, log_index,
                    pool_id, direction, amount0_wei, amount1_wei, fee_wei,
                    price_pre, price_post, tick_pre, tick_post
                ) VALUES (
                    %s, to_timestamp(%s), %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (block_number, log_index) DO NOTHING;
                """,
                (
                    ev.block_number, ev.block_timestamp, ev.tx_hash, ev.log_index,
                    s.pool_id, s.direction.value,
                    s.amount0_in_wei, s.amount1_in_wei, s.fee_amount_wei,
                    s.pool_price_pre, s.pool_price_post,
                    s.pool_tick_pre, s.pool_tick_post,
                ),
            )
        self.conn.commit()

    def on_position_opened(self, ev: PositionOpenedEvent) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO position_events (
                    block_number, block_ts, tx_hash, log_index,
                    event_kind, pool_id, nft_id, owner, l_delta, p_a, p_b
                ) VALUES (
                    %s, to_timestamp(%s), %s, %s,
                    'opened', %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (block_number, log_index) DO NOTHING;
                """,
                (
                    ev.block_number, ev.block_timestamp, ev.tx_hash, ev.log_index,
                    ev.pool_id, ev.nft_id, str(ev.owner), ev.L, ev.p_a, ev.p_b,
                ),
            )
        self.conn.commit()

    def on_position_updated(self, ev: PositionUpdatedEvent) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO position_events (
                    block_number, block_ts, tx_hash, log_index,
                    event_kind, pool_id, nft_id, l_delta,
                    fees_collected_token0_wei, fees_collected_token1_wei
                ) VALUES (
                    %s, to_timestamp(%s), %s, %s,
                    'updated', %s, %s, %s, %s, %s
                )
                ON CONFLICT (block_number, log_index) DO NOTHING;
                """,
                (
                    ev.block_number, ev.block_timestamp, ev.tx_hash, ev.log_index,
                    ev.pool_id, ev.nft_id, ev.L_delta,
                    ev.fees_collected_token0_wei, ev.fees_collected_token1_wei,
                ),
            )
        self.conn.commit()

    def on_oracle_update(self, ev: OracleUpdateEvent) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oracle_observations (
                    block_number, block_ts, oracle, pair_id, price, decimals, n_sources
                ) VALUES (
                    %s, to_timestamp(%s), %s, %s, %s, %s, %s
                );
                """,
                (
                    ev.block_number, ev.block_timestamp,
                    str(ev.oracle), ev.pair_id, ev.price, ev.decimals,
                    ev.n_sources_aggregated,
                ),
            )
        self.conn.commit()

    def on_gauge_rate_adjusted(self, ev: GaugeRateAdjustedEvent) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gauge_rate_adjustments (
                    block_number, block_ts, distributor,
                    new_rate_per_second, old_rate_per_second, epoch
                ) VALUES (
                    %s, to_timestamp(%s), %s, %s, %s, %s
                );
                """,
                (
                    ev.block_number, ev.block_timestamp,
                    str(ev.distributor),
                    ev.new_rate_per_second, ev.old_rate_per_second, ev.epoch,
                ),
            )
        self.conn.commit()
