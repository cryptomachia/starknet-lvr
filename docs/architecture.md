# Architecture

This document describes how the pieces fit together. The proposal's Figure 5
shows the same system at a higher level; this is the developer-facing view.

## Layered model

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                EXTERNAL                                   │
│                                                                           │
│  Starknet RPC          Coinbase Advanced Trade        Extended Exchange   │
│  (Pathfinder/Juno)     api.exchange.coinbase.com      api.extended...     │
│  swap & event log      OHLC reference klines          perp funding+marks  │
│                                                                           │
│  prod-api.ekubo.org    Pragma oracle feeds            DeFi Spring gauges  │
│  daily aggregates      in-protocol price reference    incentive accruals  │
└──────────────┬───────────────────┬─────────────────────────┬──────────────┘
               │                   │                         │
               ▼                   ▼                         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          INDEXING & JOINING                              │
│                                                                          │
│   scripts/pull_ekubo_data.py    pull_coinbase_ohlc.py   pull_extended... │
│   API-based daily aggregates    hourly OHLC bars        funding history  │
│                                                                          │
│   M1: Rust event indexer        time-aligned CEX joiner address-graph   │
│       (starknet-rs+tokio)       (NTP UTC microsecond)   clusterer       │
└──────────────┬─────────────────────────────────────────────┬─────────────┘
               │                                             │
               ▼                                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              STORAGE                                     │
│                                                                          │
│   Today: data/*.csv + data/*.json                                        │
│   M1+:   TimescaleDB hypertables (event_log, swap_with_marks, pool_snap) │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              COMPUTE                                     │
│                                                                          │
│   src/lvr_lab/compute/                                              │
│   ├── greeks.py         Δ, Γ, position value (Lambert 2021)              │
│   ├── lvr.py            instantaneous LVR (Milionis 2022) + TVL proxy    │
│   ├── sigma_fee.py      closed-form & Brent solver                       │
│   ├── vol_estimators.py YZ, GK, RS, Parkinson, bipower realized vol      │
│   ├── markouts.py       per-fill markout at horizons (1s/5s/30s/5min)    │
│   ├── lp_simulator.py   uniform-band & concentrated-active P&L           │
│   └── bootstrap.py      block bootstrap CIs                              │
│                                                                          │
│   src/lvr_lab/analysis/                                             │
│   ├── hypothesis_tests.py  Newey-West, Fama-MacBeth, 2SLS-IV, BH-FDR    │
│   └── cross_amm.py         unified pool object across Ekubo / v3 / LB   │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              OUTPUTS                                     │
│                                                                          │
│   scripts/run_backtest.py   →  data/wedge_timeseries.csv                 │
│                                data/wedge_summary.csv                    │
│   figures/make_figures.py   →  figures/fig{1..6}.png                     │
│   src/lvr_lab/api/     →  Vercel-hosted FastAPI read-only dashboard │
│   cairo/ekubo_greeks/       →  open-source Cairo library on Scarb registry│
└──────────────────────────────────────────────────────────────────────────┘
```

## What runs today vs. what M1 ships

| Component | Today | M1 deliverable |
|---|---|---|
| Ekubo data ingestion | Daily aggregates from public API | Per-swap Rust indexer against Pathfinder |
| Pool state replay | None — TVL proxy used | Per-block reconstruction of singleton state |
| LP simulator | Analytical only | Real on-chain position reconstruction |
| LVR | TVL-proxied (over-estimates concentrated, under-estimates wide-range) | Per-tick-crossing exact (LP_inventory_change × (p_ref − p_pool)) |
| Markouts | None | 1s/5s/30s/5min per-swap |
| σ_fee solver | Closed-form on weekly aggregates | Daily σ_fee with rolling windows |
| Cairo `ekubo-greeks` | v0.1 (Greeks + LVR rate; not deployable) | Production-ready, Scarb-registry, audited |
| Public dashboard | FastAPI stub | Vercel deployment + read API |

## Data contracts

`ekubo_data.json` — Ekubo daily-aggregate scoping pull. Schema:
```jsonc
{
  "window_start": "2026-04-30T00:00:00+00:00",
  "window_end": "2026-05-07T00:00:00+00:00",
  "binance_cache": { "ETH-USD": { "sigma_realized_ann": 0.38, "n_obs": 168 } },
  "records": [
    {
      "pair": "USDC/STRK",
      "sym0": "USDC", "sym1": "STRK",
      "addr0": "0x...", "addr1": "0x...",
      "vol_7d_usd": 7134629,
      "fees_7d_usd": 3567,
      "tvl_usd_now": 821735,
      "fee_yield_ann": 0.2263,
      "n_pools": 2,
      "daily_rows": [...],
      "sigma_realized_ann": 1.7772,
      "wedge": -1.5508
    }
  ]
}
```

`coinbase_ohlc_<symbol>.csv` — hourly OHLC bars (UTC seconds; close, open, high,
low, volume) for `eth_usd`, `btc_usd`, `strk_usd`.

`wedge_timeseries.csv` — primary backtest output. Columns: `pool, sym0, sym1,
ref_kind, fees_7d_usd, tvl_usd, vol_7d_usd, sigma_fee_ann, sigma_realized_yz,
sigma_realized_cc, sigma_realized_gk, sigma_realized_pk, wedge_yz, wedge_cc`.

`wedge_summary.csv` — bootstrap CI on the median volatile-pool wedge across
estimators. Columns: `estimator, median_wedge, ci_low, ci_high, n_pools`.

## Dependencies

Python: numpy, scipy, matplotlib, certifi (data layer); statsmodels and pandas
for analysis; fastapi+uvicorn for the dashboard. All optional groups in
`pyproject.toml`.

Cairo: Scarb 2.8.0+, the standard `starknet` package. No third-party Cairo
deps for v0.1 — production fixed-point math (Alexandria Math, cubit) deferred
to M3.

External services: none required for the demo. M1+ adds Pathfinder, optionally
Tardis.dev for tick-level CEX data.
