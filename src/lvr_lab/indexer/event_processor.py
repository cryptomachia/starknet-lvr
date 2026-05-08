"""
Event processor — the indexer's main loop.

Pulls raw events from RPC (via StarknetRpcClient), normalizes via the
appropriate normalizer for the contract, publishes to the event bus.
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..infrastructure.rpc_client import StarknetRpcClient, RpcError, RpcRateLimitError
from ..infrastructure.event_bus import EventBus
from ..infrastructure.checkpoint import CheckpointStore
from .normalizer import normalize_ekubo_event, normalize_pragma_event, normalize_gauge_event


@dataclass
class IndexerStats:
    blocks_processed: int = 0
    events_pulled: int = 0
    events_normalized: int = 0
    events_published: int = 0
    errors: int = 0
    last_processed_block: Optional[int] = None
    started_at: float = field(default_factory=time.time)

    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    def events_per_second(self) -> float:
        u = self.uptime_seconds()
        return self.events_published / u if u > 0 else 0.0


@dataclass
class PipelineConfig:
    """Per-pipeline config: which contract, which normalizer, how to identify itself."""
    name: str
    contract_address: str
    normalizer: Callable[[dict], Optional[object]]
    checkpoint_key: str
    chunk_size: int = 100
    max_chunks_per_iteration: int = 50  # avoid pulling unbounded ranges


class EventProcessor:
    """Pulls events from one contract, normalizes, publishes.

    Production deployment: one EventProcessor per (contract, pipeline) pair,
    each running in its own thread / process. The event bus fans out.
    """

    def __init__(
        self,
        rpc: StarknetRpcClient,
        bus: EventBus,
        checkpoint: CheckpointStore,
        pipeline: PipelineConfig,
    ):
        self.rpc = rpc
        self.bus = bus
        self.checkpoint = checkpoint
        self.pipeline = pipeline
        self.stats = IndexerStats()

    def run_once(self, max_blocks: Optional[int] = None) -> int:
        """Process one batch of blocks. Returns number of events published.

        Catches RPC errors and increments error stat; never raises.
        """
        try:
            head = self.rpc.block_number()
        except RpcError as e:
            self.stats.errors += 1
            return 0

        last = self.checkpoint.get(self.pipeline.checkpoint_key) or 0
        if last == 0:
            # First run — start near head to avoid syncing entire chain.
            last = max(0, head - 1000)

        from_block = last + 1
        to_block = head
        if max_blocks is not None:
            to_block = min(to_block, from_block + max_blocks - 1)
        if from_block > to_block:
            return 0

        published = 0
        cont_token: Optional[str] = None
        chunks_this_iter = 0
        while True:
            try:
                result = self.rpc.get_events(
                    address=self.pipeline.contract_address,
                    from_block=from_block,
                    to_block=to_block,
                    chunk_size=self.pipeline.chunk_size,
                    continuation_token=cont_token,
                )
            except RpcRateLimitError:
                self.stats.errors += 1
                time.sleep(1.0)
                continue
            except RpcError:
                self.stats.errors += 1
                break

            events = result.get("events", [])
            self.stats.events_pulled += len(events)

            for raw in events:
                normalized = self.pipeline.normalizer(raw)
                if normalized is None:
                    continue
                self.stats.events_normalized += 1
                delivered = self.bus.publish(normalized)
                if delivered > 0:
                    self.stats.events_published += delivered
                    published += delivered

            cont_token = result.get("continuation_token")
            chunks_this_iter += 1
            if not cont_token or chunks_this_iter >= self.pipeline.max_chunks_per_iteration:
                break

        self.stats.blocks_processed += (to_block - from_block + 1)
        self.stats.last_processed_block = to_block
        self.checkpoint.set(self.pipeline.checkpoint_key, to_block)
        return published

    def run_forever(self, poll_interval_seconds: float = 5.0) -> None:
        """Long-running mode. Polls every `poll_interval_seconds`."""
        while True:
            try:
                self.run_once()
            except Exception as e:
                self.stats.errors += 1
                import sys
                print(f"[indexer/{self.pipeline.name}] unexpected: {e}", file=sys.stderr)
            time.sleep(poll_interval_seconds)


# ---------- Pre-configured pipelines ----------
EKUBO_CORE_ADDR = "0x00000005dd3d2f4429af886cd1a3b08289dbcea99a294197e9eb43b0e0325b4b"
PRAGMA_ORACLE_ADDR = "0x2a85bd616f912537c50a49a4076db02c00b29b2cdc8a197ce92ed1837fa875b"


def ekubo_pipeline() -> PipelineConfig:
    return PipelineConfig(
        name="ekubo_core",
        contract_address=EKUBO_CORE_ADDR,
        normalizer=normalize_ekubo_event,
        checkpoint_key="ekubo_core",
    )


def pragma_pipeline() -> PipelineConfig:
    return PipelineConfig(
        name="pragma_oracle",
        contract_address=PRAGMA_ORACLE_ADDR,
        normalizer=normalize_pragma_event,
        checkpoint_key="pragma_oracle",
    )


def gauge_pipeline(distributor_address: str) -> PipelineConfig:
    return PipelineConfig(
        name=f"gauge_{distributor_address[:8]}",
        contract_address=distributor_address,
        normalizer=normalize_gauge_event,
        checkpoint_key=f"gauge_{distributor_address[:8]}",
    )
