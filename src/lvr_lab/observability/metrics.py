"""
Prometheus metrics — exported on `/metrics` from the dashboard API.

Production: scraped by the SNF Foundation's Prometheus or a self-hosted one.
Dev: not required.

We define the canonical metric set here; the dashboard imports and uses them.
Falls back to no-op counters if `prometheus_client` isn't installed.
"""

from __future__ import annotations
from typing import Optional

try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
    _HAS_PROM = True
except ImportError:
    _HAS_PROM = False

    class _NoopMetric:
        def __init__(self, *args, **kwargs): pass
        def inc(self, n=1, **labels): pass
        def dec(self, n=1, **labels): pass
        def set(self, value, **labels): pass
        def observe(self, value, **labels): pass
        def labels(self, **labels): return self
        def time(self):
            class _T:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return _T()

    Counter = Gauge = Histogram = _NoopMetric

    def generate_latest(): return b""
    CONTENT_TYPE_LATEST = "text/plain"


# ---------- Indexer metrics ----------
indexer_blocks_processed_total = Counter(
    "indexer_blocks_processed_total",
    "Total blocks processed by the indexer",
    labelnames=("pipeline",),
)

indexer_events_published_total = Counter(
    "indexer_events_published_total",
    "Total events published to the bus",
    labelnames=("pipeline", "event_type"),
)

indexer_errors_total = Counter(
    "indexer_errors_total",
    "Indexer errors by pipeline + kind",
    labelnames=("pipeline", "kind"),
)

indexer_last_block_processed = Gauge(
    "indexer_last_block_processed",
    "Highest block number fully processed",
    labelnames=("pipeline",),
)

# ---------- RPC metrics ----------
rpc_request_latency_seconds = Histogram(
    "rpc_request_latency_seconds",
    "Latency of RPC calls",
    labelnames=("method", "endpoint"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

rpc_errors_total = Counter(
    "rpc_errors_total",
    "RPC errors by method + endpoint + kind",
    labelnames=("method", "endpoint", "kind"),
)

# ---------- Compute metrics ----------
compute_sigma_fee_duration_seconds = Histogram(
    "compute_sigma_fee_duration_seconds",
    "Time spent computing σ_fee for a window",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
)

compute_lvr_duration_seconds = Histogram(
    "compute_lvr_duration_seconds",
    "Time spent computing LVR for a window",
)

# ---------- Dashboard API metrics ----------
api_requests_total = Counter(
    "api_requests_total",
    "Dashboard API requests",
    labelnames=("endpoint", "status"),
)

api_request_duration_seconds = Histogram(
    "api_request_duration_seconds",
    "API request latency",
    labelnames=("endpoint",),
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0),
)

# ---------- Helpers ----------
def export_metrics() -> bytes:
    """Return Prometheus text-format metrics. Used by the /metrics endpoint."""
    if _HAS_PROM:
        return generate_latest()
    return b""
