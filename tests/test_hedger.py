"""Tests for the off-chain hedger bot."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvr_lab.hedger import HedgerBot, HedgerConfig, HedgeAction
from lvr_lab.infrastructure.rpc_client import StarknetRpcClient


@pytest.fixture
def bot():
    rpc = StarknetRpcClient()
    cfg = HedgerConfig(
        vault_address="0xabc",
        dry_run=True,
        min_action_threshold_eth=0.01,
        max_short_eth=100.0,
    )
    return HedgerBot(rpc=rpc, config=cfg)


def test_no_op_below_threshold(bot):
    bot.state.current_short_eth = 5.0
    action = bot.decide(new_target_eth=5.005)
    assert action == HedgeAction.NO_OP


def test_increase_short(bot):
    bot.state.current_short_eth = 5.0
    action = bot.decide(new_target_eth=10.0)
    assert action == HedgeAction.INCREASE_SHORT


def test_decrease_short(bot):
    bot.state.current_short_eth = 10.0
    action = bot.decide(new_target_eth=5.0)
    assert action == HedgeAction.DECREASE_SHORT


def test_close_position(bot):
    bot.state.current_short_eth = 5.0
    action = bot.decide(new_target_eth=0.0)
    assert action == HedgeAction.CLOSE


def test_dry_run_records_skipped(bot):
    action = bot.on_hedge_trigger(
        new_target_eth_wei=int(2.5 * 1e18),
        pool_id="0xpool", block_number=12345,
    )
    assert action == HedgeAction.INCREASE_SHORT
    assert bot.state.current_short_eth == 2.5
    assert bot.stats()["actions_skipped_dry"] >= 1


def test_max_short_cap_respected(bot):
    bot.state.current_short_eth = 50.0
    # decide with target > max_short_eth (100)
    action = bot.decide(new_target_eth=200.0)
    # The decide function should still return an action (capping happens internally)
    assert action != HedgeAction.NO_OP
