"""
Starknet RPC client — multi-endpoint, retry/backoff, error normalization.

Used by the indexer and the Pragma reader. Wraps the JSON-RPC interface so
upstream code doesn't deal with raw urllib.

Provides:
  - StarknetRpcClient: round-robin across configured endpoints
  - RpcError, RpcRateLimitError, RpcContractError: normalized exceptions
  - Retry with exponential backoff
"""

from __future__ import annotations
import json
import ssl
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


_CTX = ssl.create_default_context()
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    pass


# Default public RPCs known to work as of 2026-05.
DEFAULT_RPCS = [
    "https://starknet.api.onfinality.io/public",
    "https://rpc.starknet.lava.build",
]


class RpcError(Exception):
    """All RPC failures normalize to this."""


class RpcRateLimitError(RpcError):
    """403 / 429 — back off and retry on a different endpoint."""


class RpcContractError(RpcError):
    """starknet_call returned an error — entrypoint missing, invalid calldata, etc."""

    def __init__(self, code: int, message: str):
        super().__init__(f"contract error {code}: {message}")
        self.code = code
        self.message = message


@dataclass
class RpcConfig:
    endpoints: list[str] = field(default_factory=lambda: list(DEFAULT_RPCS))
    timeout: float = 8.0
    max_retries: int = 3
    backoff_base: float = 0.5     # seconds; exponential
    user_agent: str = "lvr-lab/0.5"


class StarknetRpcClient:
    """Round-robin RPC client with retry/backoff."""

    def __init__(self, config: Optional[RpcConfig] = None):
        self.config = config or RpcConfig()
        self._cursor = 0  # round-robin pointer

    def call(self, method: str, params: Any) -> Any:
        """Issue a JSON-RPC call; raise RpcError on total failure."""
        body = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }).encode()

        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries):
            url = self._next_endpoint()
            try:
                req = urllib.request.Request(
                    url, data=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": self.config.user_agent,
                    },
                )
                with urllib.request.urlopen(
                    req, timeout=self.config.timeout, context=_CTX
                ) as r:
                    resp = json.loads(r.read())
                if "error" in resp:
                    err = resp["error"]
                    code = err.get("code", 0)
                    msg = err.get("message", "")
                    if code in (-32000, 21):
                        raise RpcContractError(code, msg)
                    last_exc = RpcError(f"jsonrpc error {code}: {msg}")
                    continue
                return resp["result"]
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    last_exc = RpcRateLimitError(f"rate limit {e.code} on {url}")
                else:
                    last_exc = RpcError(f"HTTP {e.code} on {url}: {e}")
            except urllib.error.URLError as e:
                last_exc = RpcError(f"URL error on {url}: {e}")
            except RpcContractError:
                raise  # contract errors are deterministic; don't retry
            except Exception as e:
                last_exc = RpcError(f"unexpected error on {url}: {e}")

            time.sleep(self.config.backoff_base * (2 ** attempt))

        raise RpcError(
            f"all {self.config.max_retries} attempts failed; last: {last_exc}"
        )

    def _next_endpoint(self) -> str:
        if not self.config.endpoints:
            raise RpcError("no RPC endpoints configured")
        url = self.config.endpoints[self._cursor % len(self.config.endpoints)]
        self._cursor += 1
        return url

    # ---------- Convenience high-level methods ----------
    def block_number(self) -> int:
        return self.call("starknet_blockNumber", [])

    def get_events(self, address: str, from_block: int, to_block: int,
                   chunk_size: int = 100, continuation_token: Optional[str] = None) -> dict:
        filter_obj: dict = {
            "from_block": {"block_number": from_block},
            "to_block": {"block_number": to_block},
            "address": address,
            "chunk_size": chunk_size,
        }
        if continuation_token:
            filter_obj["continuation_token"] = continuation_token
        return self.call("starknet_getEvents", {"filter": filter_obj})

    def starknet_call(self, contract_address: str, entry_point_selector: str,
                      calldata: list[str], block_id: str = "latest") -> list[str]:
        return self.call("starknet_call", {
            "request": {
                "contract_address": contract_address,
                "entry_point_selector": entry_point_selector,
                "calldata": calldata,
            },
            "block_id": block_id,
        })

    def get_block(self, block_id: int | str = "latest") -> dict:
        if isinstance(block_id, int):
            return self.call("starknet_getBlockWithTxs", {"block_id": {"block_number": block_id}})
        return self.call("starknet_getBlockWithTxs", {"block_id": block_id})
