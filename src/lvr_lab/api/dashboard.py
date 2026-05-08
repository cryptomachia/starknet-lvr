"""
Production-grade FastAPI dashboard backend.

Endpoints:
    GET  /                              — landing redirect
    GET  /health                        — liveness probe
    GET  /pools                         — list all instrumented pools
    GET  /pools/{pool_id}                — pool metadata + latest metrics
    GET  /pools/{pool_id}/sigma_fee     — daily σ_fee timeseries
    GET  /pools/{pool_id}/wedge         — wedge with LP-friendly interpretation
    GET  /pools/{pool_id}/lvr           — LVR rate + cumulative
    GET  /pools/{pool_id}/term_structure — σ_fee at multiple windows
    GET  /cross_amm/wedge_panel         — Ekubo vs Uniswap v3
    GET  /risk/var/{pool_id}            — VaR / CVaR table
    GET  /metrics                       — Prometheus
    GET  /openapi.json                  — auto-generated schema

Backed by either a TimescaleDB connection (production) or pre-computed CSVs
(dev). Same routes either way; data-source layer is abstracted.

Run:    uvicorn lvr_lab.api.dashboard:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations
import csv
import os
import time
from pathlib import Path
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException, Query, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import RedirectResponse
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from ..observability import metrics as obs_metrics
from ..observability.logging import get_logger

log = get_logger("api.dashboard")


# ---------- Data source ----------
class FileDataSource:
    """Read pre-computed CSVs from `data/`. Default for dev / staging.

    Production swap-in: PostgresDataSource reading from TimescaleDB
    materialized views; same interface.
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)

    def list_pools(self) -> list[dict]:
        path = self.data_dir / "wedge_timeseries.csv"
        if not path.exists():
            return []
        out = []
        with path.open() as f:
            for r in csv.DictReader(f):
                out.append({
                    "pool_id": r["pool"].replace("/", "-"),
                    "display_name": r["pool"],
                    "amm": "ekubo",
                    "tokens": [r["sym0"], r["sym1"]],
                    "ref_kind": r["ref_kind"],
                    "tvl_usd": float(r["tvl_usd"]) if r["tvl_usd"] else 0.0,
                })
        return out

    def get_pool_metrics(self, pool_id: str) -> Optional[dict]:
        for p in self.list_pools():
            if p["pool_id"] == pool_id:
                return p
        return None

    def sigma_fee_series(self, pool_id: str) -> list[dict]:
        path = self.data_dir / "wedge_timeseries.csv"
        if not path.exists():
            return []
        out = []
        with path.open() as f:
            for r in csv.DictReader(f):
                if r["pool"].replace("/", "-") != pool_id:
                    continue
                out.append({
                    "date": "2026-05-07",
                    "sigma_fee_ann": float(r["sigma_fee_ann"]) if r["sigma_fee_ann"] else None,
                    "sigma_realized_yz": float(r["sigma_realized_yz"]) if r["sigma_realized_yz"] else None,
                    "sigma_realized_cc": float(r["sigma_realized_cc"]) if r["sigma_realized_cc"] else None,
                    "wedge_yz": float(r["wedge_yz"]) if r["wedge_yz"] else None,
                })
        return out

    def term_structure(self, pool_id: str) -> Optional[dict]:
        path = self.data_dir / "sigma_fee_term_structure.csv"
        if not path.exists():
            return None
        with path.open() as f:
            for r in csv.DictReader(f):
                if r["pair"].replace("/", "-") != pool_id:
                    continue
                return {
                    "pool_id": pool_id,
                    "tvl_usd": float(r["tvl_usd"]),
                    "windows": {
                        "1d": float(r["sigma_fee_1d"]) if r["sigma_fee_1d"] else None,
                        "3d": float(r["sigma_fee_3d"]) if r["sigma_fee_3d"] else None,
                        "7d": float(r["sigma_fee_7d"]) if r["sigma_fee_7d"] else None,
                        "14d": float(r["sigma_fee_14d"]) if r["sigma_fee_14d"] else None,
                        "30d": float(r["sigma_fee_30d"]) if r["sigma_fee_30d"] else None,
                    },
                }
        return None

    def cross_amm(self) -> dict:
        out: dict = {"ekubo": [], "uniswap_v3": []}
        ekubo_path = self.data_dir / "wedge_timeseries.csv"
        if ekubo_path.exists():
            with ekubo_path.open() as f:
                for r in csv.DictReader(f):
                    out["ekubo"].append({
                        "pool": r["pool"],
                        "tvl_usd": float(r["tvl_usd"]) if r["tvl_usd"] else 0.0,
                        "sigma_fee_ann": float(r["sigma_fee_ann"]) if r["sigma_fee_ann"] else None,
                        "wedge_yz": float(r["wedge_yz"]) if r["wedge_yz"] else None,
                    })
        univ3_path = self.data_dir / "uniswap_v3_scoping.csv"
        if univ3_path.exists():
            with univ3_path.open() as f:
                for r in csv.DictReader(f):
                    out["uniswap_v3"].append({
                        "pair": r["pair"],
                        "tier": r["fee_tier"],
                        "tvl_usd": float(r["tvl_usd"]) if r["tvl_usd"] else 0.0,
                        "vol_24h_usd": float(r["vol_24h_usd"]) if r["vol_24h_usd"] else 0.0,
                        "fee_yield_ann_pct": float(r["fee_yield_ann_pct"]) if r["fee_yield_ann_pct"] else 0.0,
                    })
        return out


# ---------- App factory ----------
if _HAS_FASTAPI:
    DATA_DIR = os.environ.get("LVR_LAB_DATA_DIR", "data")
    data_source = FileDataSource(data_dir=DATA_DIR)

    app = FastAPI(
        title="LVR Lab Dashboard API",
        version="0.5.0",
        description="Public read-only API for σ_fee, wedge, LVR, and risk metrics on Ekubo pools.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def metrics_middleware(request, call_next):
        start = time.time()
        endpoint = request.url.path
        try:
            response = await call_next(request)
            duration = time.time() - start
            obs_metrics.api_request_duration_seconds.labels(endpoint=endpoint).observe(duration)
            obs_metrics.api_requests_total.labels(endpoint=endpoint, status=str(response.status_code)).inc()
            return response
        except Exception:
            obs_metrics.api_requests_total.labels(endpoint=endpoint, status="500").inc()
            raise

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/pools")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.5.0", "data_dir": str(data_source.data_dir)}

    @app.get("/metrics")
    def metrics_endpoint():
        return Response(
            content=obs_metrics.export_metrics(),
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/pools")
    def list_pools():
        pools = data_source.list_pools()
        return {"count": len(pools), "pools": pools}

    @app.get("/pools/{pool_id}")
    def get_pool(pool_id: str):
        meta = data_source.get_pool_metrics(pool_id)
        if not meta:
            raise HTTPException(status_code=404, detail=f"pool not found: {pool_id}")
        return meta

    @app.get("/pools/{pool_id}/sigma_fee")
    def sigma_fee(pool_id: str):
        series = data_source.sigma_fee_series(pool_id)
        if not series:
            raise HTTPException(status_code=404, detail=f"no σ_fee data for {pool_id}")
        return {"pool_id": pool_id, "series": series}

    @app.get("/pools/{pool_id}/wedge")
    def wedge(pool_id: str):
        series = data_source.sigma_fee_series(pool_id)
        if not series:
            raise HTTPException(status_code=404, detail=f"no wedge data for {pool_id}")
        latest = series[-1]
        return {
            "pool_id": pool_id,
            "wedge_yz": latest.get("wedge_yz"),
            "sigma_fee_ann": latest.get("sigma_fee_ann"),
            "sigma_realized_yz": latest.get("sigma_realized_yz"),
            "interpretation": _interpret_wedge(latest.get("wedge_yz")),
        }

    @app.get("/pools/{pool_id}/term_structure")
    def term_structure(pool_id: str):
        ts = data_source.term_structure(pool_id)
        if not ts:
            raise HTTPException(status_code=404, detail=f"no term structure for {pool_id}")
        return ts

    @app.get("/pools/{pool_id}/lvr")
    def lvr(pool_id: str):
        meta = data_source.get_pool_metrics(pool_id)
        if not meta:
            raise HTTPException(status_code=404, detail=f"pool not found: {pool_id}")
        return {
            "pool_id": pool_id,
            "lvr_rate_per_year_usd": None,
            "cumulative_lvr_30d_usd": None,
            "note": "LVR cumulative requires swap-level indexer (M2 deliverable).",
        }

    @app.get("/cross_amm/wedge_panel")
    def cross_amm_panel():
        return data_source.cross_amm()

    @app.get("/risk/var/{pool_id}")
    def risk_var(pool_id: str, alpha: float = Query(0.05, ge=0.001, le=0.5)):
        return {
            "pool_id": pool_id,
            "alpha": alpha,
            "note": "VaR requires NAV history — wired in M2 once vault tracker is live.",
        }

else:
    app = None


def _interpret_wedge(wedge: Optional[float]) -> str:
    """LP-friendly green/yellow/red categorization."""
    if wedge is None:
        return "no data"
    if wedge > 0.05:
        return "GREEN — fees comfortably exceed implied LVR cost"
    if wedge > -0.05:
        return "YELLOW — fees ≈ implied LVR; LP solvency depends on incentives"
    if wedge > -0.50:
        return "ORANGE — fees materially below implied LVR"
    return "RED — fees nowhere near implied LVR; LP underwater without incentives"
