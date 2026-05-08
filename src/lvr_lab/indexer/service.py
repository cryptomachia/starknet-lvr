"""
Indexer service — wires processor + bus + persistence together.

Production usage:
    python -m lvr_lab.indexer.service --pipelines ekubo,pragma --postgres-url $DB_URL

Local usage:
    python -m lvr_lab.indexer.service --in-memory  # no DB, just metrics
"""

from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from ..infrastructure.rpc_client import StarknetRpcClient, RpcConfig
from ..infrastructure.event_bus import EventBus
from ..infrastructure.checkpoint import (
    CheckpointStore, FileCheckpointStore, InMemoryCheckpointStore, PostgresCheckpointStore,
)
from ..infrastructure.sqlite_persistence import SqlitePersistence, SqliteCheckpointStore
from ..domain.events import (
    SwapEvent, PositionOpenedEvent, PositionUpdatedEvent, PositionClosedEvent,
    OracleUpdateEvent, GaugeRateAdjustedEvent,
)
from .event_processor import (
    EventProcessor, PipelineConfig,
    ekubo_pipeline, pragma_pipeline, gauge_pipeline,
)
from .persistence import TimescalePersistence


def _build_handlers(persistence: Optional[TimescalePersistence]) -> dict:
    """Return event-type → handler map.

    With persistence: writes events to Postgres.
    Without:          counts events for monitoring; useful for dev.
    """
    if persistence is None:
        # Just observe-and-count
        counts: dict[str, int] = {}

        def make_counter(label: str):
            def handler(ev):
                counts[label] = counts.get(label, 0) + 1
            return handler

        return {
            SwapEvent: make_counter("swap"),
            PositionOpenedEvent: make_counter("position_opened"),
            PositionUpdatedEvent: make_counter("position_updated"),
            PositionClosedEvent: make_counter("position_closed"),
            OracleUpdateEvent: make_counter("oracle_update"),
            GaugeRateAdjustedEvent: make_counter("gauge_rate_adjusted"),
            "_counts": counts,
        }

    return {
        SwapEvent: persistence.on_swap,
        PositionOpenedEvent: persistence.on_position_opened,
        PositionUpdatedEvent: persistence.on_position_updated,
        OracleUpdateEvent: persistence.on_oracle_update,
        GaugeRateAdjustedEvent: persistence.on_gauge_rate_adjusted,
    }


def main():
    parser = argparse.ArgumentParser(description="lvr-lab indexer service")
    parser.add_argument("--pipelines", default="ekubo",
                        help="comma-separated: ekubo,pragma,gauge:<addr>")
    parser.add_argument("--postgres-url", default=None,
                        help="if set, persist to Postgres; else in-memory counters")
    parser.add_argument("--sqlite-db", default=None,
                        help="if set, persist to a SQLite file (no Docker needed)")
    parser.add_argument("--checkpoint-file", default="./.indexer_checkpoint.json")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-blocks-per-iter", type=int, default=500)
    parser.add_argument("--once", action="store_true",
                        help="run a single iteration and exit (for testing)")
    args = parser.parse_args()

    # RPC client
    rpc = StarknetRpcClient(RpcConfig())

    # Persistence — three backends in order of preference: postgres > sqlite > none.
    persistence = None
    checkpoint: CheckpointStore
    if args.postgres_url:
        try:
            import psycopg
            conn = psycopg.connect(args.postgres_url)
            persistence = TimescalePersistence(conn=conn)
            checkpoint = PostgresCheckpointStore(conn=conn)
        except ImportError:
            print("psycopg not installed; falling back to file checkpoint + in-memory")
            checkpoint = FileCheckpointStore(args.checkpoint_file)
    elif args.sqlite_db:
        sp = SqlitePersistence(args.sqlite_db)
        sp.initialize()
        persistence = sp
        checkpoint = SqliteCheckpointStore(args.sqlite_db)
        print(f"[indexer] using SQLite persistence at {args.sqlite_db}")
    else:
        checkpoint = FileCheckpointStore(args.checkpoint_file)

    handlers = _build_handlers(persistence)
    bus = EventBus()
    for event_type, handler in handlers.items():
        if event_type == "_counts":
            continue
        bus.subscribe(event_type, handler)

    # Build pipelines
    pipelines: list[PipelineConfig] = []
    for spec in args.pipelines.split(","):
        spec = spec.strip()
        if spec == "ekubo":
            pipelines.append(ekubo_pipeline())
        elif spec == "pragma":
            pipelines.append(pragma_pipeline())
        elif spec.startswith("gauge:"):
            addr = spec.split(":", 1)[1]
            pipelines.append(gauge_pipeline(addr))
        else:
            print(f"unknown pipeline: {spec}", file=sys.stderr)

    if not pipelines:
        print("no pipelines configured; exiting", file=sys.stderr)
        sys.exit(1)

    processors = [EventProcessor(rpc, bus, checkpoint, p) for p in pipelines]
    print(f"[indexer] starting {len(processors)} pipelines: "
          f"{[p.pipeline.name for p in processors]}")

    if args.once:
        for proc in processors:
            n = proc.run_once(max_blocks=args.max_blocks_per_iter)
            print(f"  [{proc.pipeline.name}] published {n} events; "
                  f"stats: {proc.stats}")
        if "_counts" in handlers:
            print(f"  total event counts: {handlers['_counts']}")
        return

    # Long-running mode: round-robin processors at poll interval.
    while True:
        for proc in processors:
            try:
                proc.run_once(max_blocks=args.max_blocks_per_iter)
            except Exception as e:
                print(f"[indexer/{proc.pipeline.name}] error: {e}", file=sys.stderr)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
