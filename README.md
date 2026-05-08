# LVR Lab

**Open-source Cairo Greeks library, public LVR analytics dashboard, and reference delta-neutral vault for Ekubo on Starknet.**

> SNF Seed Grant submission · v2 (product-shaped). Read [`proposal/lvr-lab-product-proposal.pdf`](proposal/lvr-lab-product-proposal.pdf) for the proposal and [`proposal/snf-form-answers-v2.pdf`](proposal/snf-form-answers-v2.pdf) for the form responses.

## What we ship — three artifacts, three milestones

| Milestone | Public artifact | Acceptance gate |
|---|---|---|
| **M1** ($5K, 2mo) | `ekubo-greeks` v0.1 on Scarb registry · `lvrlab.xyz` dashboard alpha · Pathfinder indexer | Library `scarb add`-able + dashboard renders real σ_fee for ≥4 pools |
| **M2** ($10K, 2mo) | `ekubo-greeks` v0.5 · dashboard beta with cross-AMM panel · ≥1 partner integration (Re7 / Troves / Nimbora) | At least one external project visibly using the library |
| **M3** ($10K, 2mo) | Reference vault on testnet · `ekubo-greeks` v1.0 (post audit-light) · production dashboard · Zenodo replication DOI | Vault works on testnet; audit findings resolved; dashboard live with measurable usage |

This is the v2 framing of the work in this repo. Same code, same math, same data — different cover sheet, aligned with what SNF actually funds (Herodotus, Pragma, Bountive — all shipped contracts; research was a byproduct).

## What's already built (real data, runs end-to-end before applying)

| Component | Status | Verify |
|---|---|---|
| `ekubo-greeks` Cairo skeleton | v0.1-rc | `cairo/ekubo_greeks/src/*.cairo` |
| Python reference math kernels | 125/125 tests pass | `make test` |
| Cairo `ekubo_greeks` library | 14/14 tests pass | `cd cairo/ekubo_greeks && scarb cairo-test` |
| Ekubo daily-aggregate scoping | 14 pools real | `data/ekubo_scoping.csv` |
| Coinbase 30-day OHLC | 721 hourly bars | `data/coinbase_ohlc_*.csv` |
| Starknet RPC event pull | 41 events / 30 blocks | `data/starknet_events_sample.json` |
| Pragma oracle reads (live) | ETH/USD basis −5.4 bps | `data/pragma_prices.csv` |
| Uniswap v3 cross-AMM | 10 pools | `data/uniswap_v3_scoping.csv` |
| Vault backtest (4 strategies) | NAV curves, real | `figures/fig8_vault_strategies.png` |
| H1 result on USDC/USDT 1bp | t = +9.14, p ≈ 0 | `data/h1_h4_results.csv` |
| σ_fee term structure | 5 windows × 4 pools | `figures/fig9_sigma_fee_term.png` |

`make repro` reruns the entire pipeline; `make test` runs the unit tests.

## Three deliverables in detail

### 1. `ekubo-greeks` Cairo library
The dependency layer for every future Starknet vault, ALMM strategy, hedger bot, and risk dashboard. Audit-light, published on the Scarb registry. Replaces Ekubo's currently-unaudited oracle extension as a safer Cairo-native source for position Greeks, IL, and instantaneous LVR. Math anchored to Lambert (2021) and Milionis et al. (2022) closed forms, cross-validated against the Python reference in this codebase.

### 2. LVR Lab Dashboard at `lvrlab.xyz`
Public web app any LP, vault curator, or DeFi analyst can read to answer one question: **"is this Ekubo pool over- or under-paying me for the LVR risk I'm bearing?"** Real-time per-pool σ_fee / σ_realized / wedge series, LP-friendly visualizations, cross-AMM panel showing Uniswap v3 USDC/WETH alongside Ekubo USDC/ETH side-by-side, public read API rate-limited to 60 req/min. The Starknet equivalent of a16z's [LVR Explorer](https://lvr-explorer.com/) for Uniswap.

### 3. Reference delta-neutral vault on Starknet sepolia
Cairo contract that demonstrates `ekubo-greeks` in production. Holds an Ekubo USDC/ETH LP, computes its delta on-chain, emits hedge-trigger events readable by an off-chain hedger bot (Python). Testnet-only during the grant window; mainnet decision is post-grant. The contract is the proof of product, not the product itself — like Bountive's prize-savings demonstration.

## Repository layout

```
lvr-lab-v2/
├── README.md                          ← you are here
├── proposal/
│   ├── lvr-lab-product-proposal.{md,pdf}    ← the SNF Seed Grant proposal
│   └── snf-form-answers-v2.{md,pdf}         ← field-by-field form text
├── data/                              ← real Ekubo + Coinbase + Pragma + Extended + RPC pulls
├── figures/                           ← 9 publication-quality figures
├── scripts/                           ← all data pulls + backtests
├── src/lvr_lab/                       ← domain · compute · analysis · indexer · backtest · risk · api · hedger
├── cairo/ekubo_greeks/                ← Cairo Greeks/IL/LVR library
├── cairo/vault/                       ← reference delta-neutral vault contract
├── dashboard/frontend/                ← LVR Lab dashboard HTML/JS frontend
├── db/migrations/                     ← TimescaleDB schema (Postgres production)
├── tests/                             ← 125 Python pytest tests, all green
└── docs/
    ├── architecture.md
    ├── findings.md                    ← preliminary results from real data
    ├── v2_findings.md                 ← outputs of the elite-quant additions
    ├── critical_review.md             ← the elite-quant gap audit
    └── depth_audit.{md,pdf}           ← per-module depth audit
```

## Citation

```
@misc{huang2026lvrlab,
  author       = {Eddie Huang},
  title        = {{LVR Lab}: Open-source LVR analytics for Ekubo on Starknet},
  year         = 2026,
  howpublished = {SNF Seed Grant submission, Starknet Foundation},
  email        = {eddiehuang2886@gmail.com}
}
```

## License

Apache-2.0. See `LICENSE` (added at first public release in M1).
