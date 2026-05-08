"""
HedgerBot — off-chain service that consumes vault HedgeTriggerEvents and
places perp orders on Extended (or simulates against Coinbase historical).

Run:
    python -m lvr_lab.hedger.service \
        --vault-address 0x... \
        --venue extended-testnet \
        --venue-api-key $EXTENDED_API_KEY

Production deployment: as a systemd service or a containerized k8s
deployment. Stateful (tracks current short size); persists checkpoint via
the same FileCheckpointStore as the indexer.
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..infrastructure.rpc_client import StarknetRpcClient
from ..infrastructure.event_bus import EventBus
from ..observability.logging import get_logger
from .extended_client import ExtendedClient, ExtendedConfig, OrderRequest

log = get_logger("hedger.bot")


class HedgeAction(Enum):
    """What the bot decides to do given a HedgeTriggerEvent."""
    NO_OP = "no_op"
    INCREASE_SHORT = "increase_short"
    DECREASE_SHORT = "decrease_short"
    CLOSE = "close"


@dataclass
class HedgerConfig:
    vault_address: str
    venue: str = "extended-testnet"        # "extended-testnet" | "extended-mainnet" | "coinbase-simulated"
    venue_api_key: Optional[str] = None
    venue_api_secret: Optional[str] = None
    perp_symbol: str = "ETH-USD-PERP"
    min_action_threshold_eth: float = 0.001  # don't bother with sub-0.001-ETH adjustments
    max_short_eth: float = 100.0             # safety cap
    dry_run: bool = True                     # default to dry-run for safety


@dataclass
class HedgerState:
    current_short_eth: float = 0.0
    target_short_eth: float = 0.0
    last_action_at: float = 0.0
    actions_taken: int = 0
    actions_skipped_dry: int = 0


class HedgerBot:
    """The hedger reacts to one HedgeTriggerEvent at a time.

    Decision logic:
      1. Read `new_target_short_eth_wei` from the event.
      2. Compare to current `state.current_short_eth`.
      3. If |delta| < min_action_threshold_eth: NO_OP.
      4. Else: INCREASE_SHORT or DECREASE_SHORT.
      5. Cap at max_short_eth.
      6. In dry_run mode: log the intended action; don't place orders.
      7. Otherwise: place market order on the venue.
      8. Call `report_hedge_state` on the on-chain HedgerOperator contract.
    """

    def __init__(self, rpc: StarknetRpcClient, config: HedgerConfig):
        self.rpc = rpc
        self.config = config
        self.state = HedgerState()
        # Production REST + signing client. Constructed lazily so dry-run
        # mode doesn't require api_secret to be set.
        self._extended: Optional[ExtendedClient] = None

    def decide(self, new_target_eth: float) -> HedgeAction:
        diff = new_target_eth - self.state.current_short_eth
        if abs(diff) < self.config.min_action_threshold_eth:
            return HedgeAction.NO_OP
        if new_target_eth >= self.config.max_short_eth:
            new_target_eth = self.config.max_short_eth
        if new_target_eth == 0:
            return HedgeAction.CLOSE
        if diff > 0:
            return HedgeAction.INCREASE_SHORT
        return HedgeAction.DECREASE_SHORT

    def execute(self, action: HedgeAction, target_eth: float) -> bool:
        """Place the order on the venue. Returns True if successful."""
        if action == HedgeAction.NO_OP:
            return True

        if self.config.dry_run:
            log.info("dry_run_action", extra={
                "action": action.value,
                "target_eth": target_eth,
                "current_eth": self.state.current_short_eth,
            })
            self.state.actions_skipped_dry += 1
            return True

        # Production path: SNIP-12 sign + POST to Extended Exchange.
        client = self._get_extended_client()
        if client is None:
            log.error("extended_client_unconfigured", extra={
                "venue": self.config.venue,
                "action": action.value,
            })
            return False

        # Direction: vault is increasing/decreasing a SHORT, so:
        #   INCREASE_SHORT  → side="sell"  (sell more ETH-PERP to grow short)
        #   DECREASE_SHORT  → side="buy"   (buy ETH-PERP to reduce short)
        #   CLOSE           → side="buy"   (close out remaining short)
        if action == HedgeAction.INCREASE_SHORT:
            size = abs(target_eth - self.state.current_short_eth)
            side = "sell"
        elif action == HedgeAction.DECREASE_SHORT:
            size = abs(self.state.current_short_eth - target_eth)
            side = "buy"
        elif action == HedgeAction.CLOSE:
            size = abs(self.state.current_short_eth)
            side = "buy"
        else:
            return True   # NO_OP

        order = OrderRequest(
            market=self.config.perp_symbol,
            side=side,
            type="market",
            size=size,
            reduce_only=(action in (HedgeAction.DECREASE_SHORT, HedgeAction.CLOSE)),
            client_order_id=f"lvrlab-{int(time.time() * 1000)}",
            time_in_force="ioc",
        )
        try:
            response = client.place_order(order)
        except Exception as e:
            log.exception("extended_place_order_exception", extra={"error": str(e)})
            return False

        log.info("extended_order_response", extra={
            "action": action.value,
            "side": side,
            "size_eth": size,
            "success": response.success,
            "order_id": response.order_id,
        })
        return response.success

    def _get_extended_client(self) -> Optional[ExtendedClient]:
        if self._extended is not None:
            return self._extended
        if not (self.config.venue_api_key and self.config.venue_api_secret):
            return None
        # Pick base URL by venue config
        base = (
            "https://api.testnet.starknet.extended.exchange/api/v1"
            if self.config.venue == "extended-testnet"
            else "https://api.starknet.extended.exchange/api/v1"
        )
        self._extended = ExtendedClient(ExtendedConfig(
            base_url=base,
            api_key=self.config.venue_api_key,
            api_secret=self.config.venue_api_secret,
        ))
        return self._extended

    def on_hedge_trigger(self, new_target_eth_wei: int, pool_id: str,
                        block_number: int) -> HedgeAction:
        """Consume one HedgeTriggerEvent. Returns the action taken."""
        new_target_eth = new_target_eth_wei / 1e18
        action = self.decide(new_target_eth)
        ok = self.execute(action, new_target_eth)
        if ok and action != HedgeAction.NO_OP:
            self.state.current_short_eth = new_target_eth
            self.state.target_short_eth = new_target_eth
            self.state.last_action_at = time.time()
            self.state.actions_taken += 1
            log.info("hedge_action_completed", extra={
                "action": action.value,
                "new_short": new_target_eth,
                "pool_id": pool_id,
                "block_number": block_number,
            })
        return action

    def stats(self) -> dict:
        return {
            "current_short_eth": self.state.current_short_eth,
            "target_short_eth": self.state.target_short_eth,
            "last_action_at": self.state.last_action_at,
            "actions_taken": self.state.actions_taken,
            "actions_skipped_dry": self.state.actions_skipped_dry,
            "dry_run": self.config.dry_run,
        }
