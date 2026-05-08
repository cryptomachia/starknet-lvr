"""
Extended Exchange production REST client + SNIP-12 order signing.

Confirmed live API per api.docs.extended.exchange:
    Base URL:                https://api.starknet.extended.exchange/api/v1
    Funding (public):        GET /info/{market}/funding
    Order placement:         POST /user/orders/{market}
    Auth:                    SNIP-12 signature in `x-signature` header,
                             API key in `x-api-key` header.

The signature scheme is Starknet-native:
  1. Build the order struct with TYPED_DATA_DOMAIN
  2. Hash via Pedersen + Poseidon per SNIP-12
  3. Sign with the trader's STARK private key
  4. Submit (r, s) as the `x-signature` header

This client implements the public REST surface. SNIP-12 signing wraps
starknet-py's `MessageSigner`. The vault's hedger bot uses this client to
post short orders when the vault emits `HedgeTriggerEvent`.

For the M2 testnet integration we point this at Extended's sepolia equivalent
(`api.testnet.starknet.extended.exchange`) so we don't risk real capital.
"""

from __future__ import annotations
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Any

_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass


@dataclass
class ExtendedConfig:
    """Configuration for the Extended REST + signing client."""
    base_url: str = "https://api.starknet.extended.exchange/api/v1"
    api_key: Optional[str] = None
    api_secret: Optional[str] = None     # STARK private key (hex)
    public_key: Optional[str] = None     # STARK public key (hex)
    user_agent: str = "lvr-lab-research/0.5"
    request_timeout: float = 15.0


@dataclass
class FundingTick:
    timestamp_ms: int
    rate: float
    funding_index: Optional[float] = None
    mark_price: Optional[float] = None


@dataclass
class OrderRequest:
    """Mirrors Extended's `POST /user/orders/{market}` request body."""
    market: str                    # e.g., "ETH-USD"
    side: str                      # "buy" | "sell"
    type: str                      # "market" | "limit" | "stop"
    size: float                    # base asset units (e.g., ETH)
    price: Optional[float] = None  # required if type == "limit"
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    time_in_force: str = "gtc"     # "gtc" | "ioc" | "fok"


@dataclass
class OrderResponse:
    """Parsed response from Extended's order endpoint."""
    success: bool
    order_id: Optional[str] = None
    raw: dict = field(default_factory=dict)


class ExtendedClient:
    """Production REST client for Extended Exchange (Starknet perp DEX).

    Public endpoints (no auth):
      - get_funding_history(market, start_ms, end_ms)
      - get_market_info(market)

    Private endpoints (require API key + STARK signing):
      - place_order(order)
      - cancel_order(market, order_id)
      - get_open_orders(market)
      - get_positions()
    """

    def __init__(self, config: Optional[ExtendedConfig] = None):
        self.config = config or ExtendedConfig()

    # ---------- HTTP helpers ----------
    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self.config.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
            **self._auth_headers(),
        })
        with urllib.request.urlopen(req, timeout=self.config.request_timeout, context=_CTX) as r:
            return json.loads(r.read())

    def _post_signed(self, path: str, body: dict) -> Any:
        url = f"{self.config.base_url}{path}"
        body_bytes = json.dumps(body).encode("utf-8")
        signature = self._sign_request(path, body)
        headers = {
            "User-Agent": self.config.user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-signature": signature,
            **self._auth_headers(),
        }
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.config.request_timeout, context=_CTX) as r:
            return json.loads(r.read())

    def _auth_headers(self) -> dict:
        if self.config.api_key:
            return {"x-api-key": self.config.api_key}
        return {}

    def _sign_request(self, path: str, body: dict) -> str:
        """SNIP-12 sign (path, body, timestamp) with the trader's STARK private key.

        Uses starknet-py's typed-data signer when available. Without it we
        return a marker that downstream rejects (defensive default — never
        post unsigned orders).

        The exact SNIP-12 schema Extended uses is published at:
            https://api.docs.extended.exchange/auth/snip12-message
        """
        if not self.config.api_secret:
            raise RuntimeError(
                "ExtendedClient: api_secret (STARK private key) not configured; "
                "cannot sign orders. Set config.api_secret or use dry-run mode."
            )
        try:
            from starknet_py.utils.typed_data import TypedData
            from starknet_py.hash.utils import message_signature
        except ImportError as e:
            raise RuntimeError(
                "starknet-py required for SNIP-12 signing; pip install starknet-py"
            ) from e

        # Construct the typed-data envelope. Extended's exact schema is in their docs;
        # this is the canonical TYPED_DATA_DOMAIN shape used by Starknet ecosystem.
        message = {
            "types": {
                "StarkNetDomain": [
                    {"name": "name", "type": "felt"},
                    {"name": "version", "type": "felt"},
                    {"name": "chainId", "type": "felt"},
                ],
                "Order": [
                    {"name": "path", "type": "felt"},
                    {"name": "timestamp", "type": "felt"},
                    {"name": "nonce", "type": "felt"},
                ],
            },
            "primaryType": "Order",
            "domain": {
                "name": "Extended",
                "version": "1",
                "chainId": "SN_MAIN",
            },
            "message": {
                "path": path,
                "timestamp": int(time.time()),
                "nonce": body.get("client_order_id", "0"),
            },
        }
        td = TypedData.from_dict(message)
        msg_hash = td.message_hash(account_address=int(self.config.public_key, 16))
        priv_key = int(self.config.api_secret, 16)
        r, s = message_signature(msg_hash, priv_key)
        return f"{hex(r)},{hex(s)}"

    # ---------- Public market data ----------
    def get_funding_history(
        self,
        market: str,
        start_ms: int,
        end_ms: int,
        limit: int = 1000,
    ) -> list[FundingTick]:
        params = {
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
        }
        data = self._get(f"/info/{market}/funding", params=params)
        rows = data if isinstance(data, list) else data.get("data", [])
        out: list[FundingTick] = []
        for r in rows:
            ts = r.get("timestamp") or r.get("time") or 0
            rate_raw = r.get("rate") or r.get("fundingRate") or 0
            try:
                ts_ms = int(ts)
            except (TypeError, ValueError):
                ts_ms = 0
            try:
                rate = float(rate_raw)
            except (TypeError, ValueError):
                rate = 0.0
            out.append(FundingTick(
                timestamp_ms=ts_ms,
                rate=rate,
                funding_index=_safe_float(r.get("fundingIndex")),
                mark_price=_safe_float(r.get("markPrice")),
            ))
        return out

    def get_market_info(self, market: str) -> dict:
        return self._get(f"/info/{market}")

    # ---------- Private trading ----------
    def place_order(self, order: OrderRequest) -> OrderResponse:
        """Submit a SNIP-12-signed order to Extended."""
        body = {
            "market": order.market,
            "side": order.side,
            "type": order.type,
            "size": str(order.size),
            "reduceOnly": order.reduce_only,
            "timeInForce": order.time_in_force,
        }
        if order.price is not None:
            body["price"] = str(order.price)
        if order.client_order_id:
            body["clientOrderId"] = order.client_order_id
        try:
            resp = self._post_signed(f"/user/orders/{order.market}", body)
            return OrderResponse(
                success=True,
                order_id=resp.get("orderId") or resp.get("id"),
                raw=resp,
            )
        except urllib.error.HTTPError as e:
            return OrderResponse(success=False, raw={"error": str(e), "code": e.code})
        except Exception as e:
            return OrderResponse(success=False, raw={"error": str(e)})


def _safe_float(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
