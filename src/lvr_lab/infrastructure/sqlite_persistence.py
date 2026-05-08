"""
SQLite-backed persistence — drop-in for the Postgres path when running locally
without Docker. Same `on_*` event-handler signatures as `TimescalePersistence`.

Schema is a SQLite-flavored subset of `db/migrations/001_initial.sql`:
  - swap_events
  - position_events
  - oracle_observations
  - gauge_rate_adjustments
  - indexer_checkpoint

No hypertables, no continuous aggregates — those need Postgres+Timescale. For
local development and testing, plain tables + indexes are sufficient. The
existing analysis scripts work against either backend (they query CSVs in
this codebase, but production will route them at the SQL adapter via
`/pools/*` API endpoints).

Usage:
    from lvr_lab.infrastructure.sqlite_persistence import SqlitePersistence
    p = SqlitePersistence("/tmp/lvr.db")
    p.initialize()  # idempotent CREATE TABLE IF NOT EXISTS
    p.on_swap(swap_event)
"""

from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from ..domain.events import (
    SwapEvent, PositionOpenedEvent, PositionUpdatedEvent,
    PositionClosedEvent, OracleUpdateEvent, GaugeRateAdjustedEvent,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS indexer_checkpoint (
    pipeline   TEXT PRIMARY KEY,
    last_block INTEGER NOT NULL,
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE TABLE IF NOT EXISTS swap_events (
    block_number  INTEGER NOT NULL,
    block_ts      REAL    NOT NULL,
    tx_hash       TEXT    NOT NULL,
    log_index     INTEGER NOT NULL,
    pool_id       TEXT    NOT NULL,
    direction     TEXT    NOT NULL,
    amount0_wei   TEXT    NOT NULL,
    amount1_wei   TEXT    NOT NULL,
    fee_wei       TEXT    NOT NULL,
    price_pre     REAL,
    price_post    REAL,
    tick_pre      INTEGER,
    tick_post     INTEGER,
    PRIMARY KEY (block_number, log_index)
);
CREATE INDEX IF NOT EXISTS idx_swap_pool_ts ON swap_events (pool_id, block_ts DESC);

CREATE TABLE IF NOT EXISTS position_events (
    block_number  INTEGER NOT NULL,
    block_ts      REAL    NOT NULL,
    tx_hash       TEXT    NOT NULL,
    log_index     INTEGER NOT NULL,
    event_kind    TEXT    NOT NULL CHECK (event_kind IN ('opened','updated','closed')),
    pool_id       TEXT    NOT NULL,
    nft_id        INTEGER,
    owner         TEXT,
    l_delta       REAL,
    p_a           REAL,
    p_b           REAL,
    fees_collected_token0_wei TEXT DEFAULT '0',
    fees_collected_token1_wei TEXT DEFAULT '0',
    PRIMARY KEY (block_number, log_index)
);

CREATE TABLE IF NOT EXISTS oracle_observations (
    block_number  INTEGER NOT NULL,
    block_ts      REAL    NOT NULL,
    oracle        TEXT    NOT NULL,
    pair_id       INTEGER NOT NULL,
    price         REAL    NOT NULL,
    decimals      INTEGER NOT NULL,
    n_sources     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (block_number, oracle, pair_id)
);

CREATE TABLE IF NOT EXISTS gauge_rate_adjustments (
    block_number          INTEGER NOT NULL,
    block_ts              REAL    NOT NULL,
    distributor           TEXT    NOT NULL,
    new_rate_per_second   REAL    NOT NULL,
    old_rate_per_second   REAL    NOT NULL,
    epoch                 INTEGER,
    PRIMARY KEY (block_number, distributor)
);
"""


class SqlitePersistence:
    """SQLite-backed event persistence. Same interface as TimescalePersistence."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")

    def initialize(self) -> None:
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def cursor(self):
        cur = self.conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ---------- Event handlers ----------
    def on_swap(self, ev: SwapEvent) -> None:
        s = ev.swap
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO swap_events (
                    block_number, block_ts, tx_hash, log_index,
                    pool_id, direction, amount0_wei, amount1_wei, fee_wei,
                    price_pre, price_post, tick_pre, tick_post
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    ev.block_number, ev.block_timestamp, ev.tx_hash, ev.log_index,
                    s.pool_id, s.direction.value,
                    str(s.amount0_in_wei), str(s.amount1_in_wei), str(s.fee_amount_wei),
                    s.pool_price_pre, s.pool_price_post,
                    s.pool_tick_pre, s.pool_tick_post,
                ),
            )

    def on_position_opened(self, ev: PositionOpenedEvent) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO position_events (
                    block_number, block_ts, tx_hash, log_index,
                    event_kind, pool_id, nft_id, owner, l_delta, p_a, p_b
                ) VALUES (?, ?, ?, ?, 'opened', ?, ?, ?, ?, ?, ?);
                """,
                (
                    ev.block_number, ev.block_timestamp, ev.tx_hash, ev.log_index,
                    ev.pool_id, ev.nft_id, str(ev.owner), ev.L, ev.p_a, ev.p_b,
                ),
            )

    def on_position_updated(self, ev: PositionUpdatedEvent) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO position_events (
                    block_number, block_ts, tx_hash, log_index,
                    event_kind, pool_id, nft_id, l_delta,
                    fees_collected_token0_wei, fees_collected_token1_wei
                ) VALUES (?, ?, ?, ?, 'updated', ?, ?, ?, ?, ?);
                """,
                (
                    ev.block_number, ev.block_timestamp, ev.tx_hash, ev.log_index,
                    ev.pool_id, ev.nft_id, ev.L_delta,
                    str(ev.fees_collected_token0_wei), str(ev.fees_collected_token1_wei),
                ),
            )

    def on_oracle_update(self, ev: OracleUpdateEvent) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO oracle_observations (
                    block_number, block_ts, oracle, pair_id, price, decimals, n_sources
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    ev.block_number, ev.block_timestamp,
                    str(ev.oracle), ev.pair_id, ev.price, ev.decimals,
                    ev.n_sources_aggregated,
                ),
            )

    def on_gauge_rate_adjusted(self, ev: GaugeRateAdjustedEvent) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO gauge_rate_adjustments (
                    block_number, block_ts, distributor,
                    new_rate_per_second, old_rate_per_second, epoch
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    ev.block_number, ev.block_timestamp,
                    str(ev.distributor),
                    ev.new_rate_per_second, ev.old_rate_per_second, ev.epoch,
                ),
            )

    # ---------- Query helpers ----------
    def count_swaps(self) -> int:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM swap_events")
            return cur.fetchone()[0]

    def latest_block(self) -> Optional[int]:
        with self.cursor() as cur:
            cur.execute("SELECT MAX(block_number) FROM swap_events")
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None

    def daily_volume_by_pool(self, pool_id: str) -> list[tuple[str, int, int]]:
        """Daily aggregate (date_iso, n_swaps, total_fees_wei) for a pool."""
        with self.cursor() as cur:
            cur.execute(
                """
                SELECT
                    date(block_ts, 'unixepoch') as day,
                    COUNT(*),
                    SUM(CAST(fee_wei AS INTEGER))
                FROM swap_events
                WHERE pool_id = ?
                GROUP BY day
                ORDER BY day;
                """,
                (pool_id,),
            )
            return [(r[0], r[1], r[2] or 0) for r in cur.fetchall()]


class SqliteCheckpointStore:
    """SQLite-backed checkpoint store, same interface as PostgresCheckpointStore."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        # Schema must already exist (call SqlitePersistence.initialize())

    def get(self, pipeline: str) -> Optional[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT last_block FROM indexer_checkpoint WHERE pipeline = ?",
                    (pipeline,))
        row = cur.fetchone()
        return row[0] if row else None

    def set(self, pipeline: str, block: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO indexer_checkpoint (pipeline, last_block)
            VALUES (?, ?)
            ON CONFLICT (pipeline)
            DO UPDATE SET last_block = excluded.last_block,
                          updated_at = strftime('%s', 'now');
            """,
            (pipeline, block),
        )
