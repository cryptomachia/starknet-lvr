"""Tests for the P2 indexer + infrastructure layer."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.infrastructure.event_bus import EventBus
from lvr_lab.infrastructure.checkpoint import (
    InMemoryCheckpointStore, FileCheckpointStore,
)
from lvr_lab.domain.events import SwapEvent, OracleUpdateEvent
from lvr_lab.domain import Swap, SwapDirection
from lvr_lab.indexer.normalizer import (
    normalize_ekubo_event, normalize_pragma_event, normalize_gauge_event,
)


# ---------- EventBus ----------
def test_eventbus_publishes_to_subscribers():
    bus = EventBus()
    received = []
    bus.subscribe(SwapEvent, lambda ev: received.append(ev))
    s = Swap(
        pool_id="0xpool", direction=SwapDirection.TOKEN0_TO_TOKEN1,
        amount0_in_wei=100, amount1_in_wei=0, fee_amount_wei=1, fee_token_is_0=True,
        pool_price_pre=2000.0, pool_price_post=1995.0, pool_tick_pre=0, pool_tick_post=0,
        block_number=1, block_timestamp=1.0, tx_hash="0x", log_index=0,
    )
    ev = SwapEvent(block_number=1, block_timestamp=1.0, tx_hash="0x", log_index=0, swap=s)
    n = bus.publish(ev)
    assert n == 1
    assert len(received) == 1


def test_eventbus_isolates_handler_errors():
    bus = EventBus()
    received = []
    bus.subscribe(SwapEvent, lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(SwapEvent, lambda ev: received.append(ev))
    s = Swap(
        pool_id="0x", direction=SwapDirection.TOKEN0_TO_TOKEN1,
        amount0_in_wei=0, amount1_in_wei=0, fee_amount_wei=0, fee_token_is_0=True,
        pool_price_pre=1, pool_price_post=1, pool_tick_pre=0, pool_tick_post=0,
        block_number=1, block_timestamp=1.0, tx_hash="0x", log_index=0,
    )
    ev = SwapEvent(block_number=1, block_timestamp=1.0, tx_hash="0x", log_index=0, swap=s)
    bus.publish(ev)
    # The good handler still ran
    assert len(received) == 1
    assert bus.stats["errors"] == 1


def test_eventbus_unsubscribe():
    bus = EventBus()
    received = []
    handler = lambda ev: received.append(ev)
    bus.subscribe(SwapEvent, handler)
    bus.unsubscribe(SwapEvent, handler)
    s = Swap(
        pool_id="0x", direction=SwapDirection.TOKEN0_TO_TOKEN1,
        amount0_in_wei=0, amount1_in_wei=0, fee_amount_wei=0, fee_token_is_0=True,
        pool_price_pre=1, pool_price_post=1, pool_tick_pre=0, pool_tick_post=0,
        block_number=1, block_timestamp=1.0, tx_hash="0x", log_index=0,
    )
    bus.publish(SwapEvent(block_number=1, block_timestamp=1.0, tx_hash="0x", log_index=0, swap=s))
    assert len(received) == 0


# ---------- Checkpoint stores ----------
def test_inmemory_checkpoint_get_set():
    cp = InMemoryCheckpointStore()
    assert cp.get("p1") is None
    cp.set("p1", 1000)
    assert cp.get("p1") == 1000
    cp.set("p1", 2000)
    assert cp.get("p1") == 2000


def test_file_checkpoint_persistence(tmp_path):
    cp = FileCheckpointStore(tmp_path / "cp.json")
    cp.set("ekubo", 1234)
    cp2 = FileCheckpointStore(tmp_path / "cp.json")
    assert cp2.get("ekubo") == 1234


# ---------- Normalizer ----------
def test_normalizer_returns_none_for_unknown_selector():
    raw = {
        "keys": ["0xdeadbeef"],
        "data": [],
        "block_number": 1,
        "block_timestamp": 0,
        "transaction_hash": "0x",
    }
    assert normalize_ekubo_event(raw) is None
    assert normalize_pragma_event(raw) is None
    assert normalize_gauge_event(raw) is None


def test_normalizer_returns_none_for_empty_keys():
    raw = {"keys": [], "data": [], "block_number": 1, "block_timestamp": 0, "transaction_hash": "0x"}
    assert normalize_ekubo_event(raw) is None


def test_normalizer_handles_real_ekubo_swapped_event():
    """Format observed live on Starknet mainnet."""
    raw = {
        "keys": ["0x157717768aca88da4ac4279765f09f4d0151823d573537fbbeb950cdbd9a870"],
        "data": [
            "0x43e4f09c32d13d43a880e85f69f7de93ceda62d6cf2581a582c6db635548fdc",  # locker / pool ref
            "0x3fe2b97c1fd336e750087d68b9b867997fd64a2661ff3ca5a7c771641e8e7ac",  # WBTC
            "0x75afe6402ad5a5c20dd25e10ec3b3986acaa647b77e4ae24b0cbc9a54a27a87",  # EKUBO
            "0xc49ba5e353f7d00000000000000000",
            "0x56a4c",
            "0x43e4f09c32d13d43a880e85f69f7de93ceda62d6cf2581a582c6db635548fdc",
            "0x5ac", "0x0", "0x0", "0x1000003f7f1380b75",
            "0x0", "0x0", "0x5ac", "0x0", "0x2218a0edf156c59a",
            "0x1", "0xc5307820080eb98e9782da65a5ed6c7a", "0x26e8aec",
            "0x216cb9f", "0x0", "0x2adb40304be",
        ],
        "block_number": 9562838,
        "block_timestamp": 1778200000,
        "transaction_hash": "0x4ec7522f9f3a0ea14c9219545bcfd561959b348eea731eb6bae623bbbe3de6a",
        "from_address": "0x5dd3d2f4429af886cd1a3b08289dbcea99a294197e9eb43b0e0325b4b",
    }
    ev = normalize_ekubo_event(raw)
    assert ev is not None
    assert ev.event_type == "swap"
    assert ev.block_number == 9562838


# ---------- IndexerStats ----------
def test_indexer_stats_defaults():
    from lvr_lab.indexer.event_processor import IndexerStats
    s = IndexerStats()
    assert s.blocks_processed == 0
    assert s.events_published == 0
    assert s.uptime_seconds() >= 0
    assert s.events_per_second() == 0.0
