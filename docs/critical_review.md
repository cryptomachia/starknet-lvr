# Critical review — what an elite quant reviewer will push on

This document inventories the technical objections a skeptical reviewer
(senior quant at Jump, JS, HRT, or a market-microstructure academic) is
likely to raise against the SNF Seed Grant proposal, and how the
implementation addresses each one.

The categories: **Addressed** (handled in code today), **Mitigated** (handled
with documented caveats), **M1 deliverable** (acknowledged gap, scoped into
M1 of the grant).

---

## 1. σ_realized estimator quality

**Objection.** Close-to-close stdev on 168 hourly bars is the worst available
estimator at this sample size. It throws away the OHLC information that the
exchange already publishes. Yang-Zhang or Garman-Klass converges 5–14× faster.

**Status: Addressed.** `src/lvr_lab/compute/vol_estimators.py` ships YZ,
GK, Rogers-Satchell, Parkinson, and a bipower-variation jump-robust estimator.
The backtest reports all of them; the headline figure uses Yang-Zhang. The
test suite verifies YZ has lower sampling variance than CC at small n.

---

## 2. The wedge is uncertain — where are the CIs?

**Objection.** "Median wedge −31 pp" is a point estimate over 14 pools and
one 7-day window. With ρ ≈ 0.3 daily autocorrelation, you cannot quote that
number without a confidence interval that respects dependence.

**Status: Addressed.** `src/lvr_lab/compute/bootstrap.py` implements a
block bootstrap (Politis-Romano) with adaptive block length b = ⌈n^{1/3}⌉.
The `run_backtest.py` script reports both YZ and close-to-close median
wedges with 95% block-bootstrap CIs.

---

## 3. TVL-proxy LVR is wrong

**Objection.** You divide annualized fees by **current snapshot TVL** to
get fee yield, then declare LVR ∝ σ². Real LVR depends on the **active-tick
liquidity** L, which is path-dependent and only computable from singleton
state. Your scoping figure is biased: it overstates LVR for narrow-range LPs
and understates it for wide-range LPs.

**Status: Mitigated for the demo; M1 deliverable for production.**
`compute/lvr.py::lvr_rate_proxy_from_tvl` documents the proxy and its bias.
`compute/lvr.py::lvr_rate_in_range` implements the exact Milionis formula
(σ²·L·√p / 4) for use once L is recoverable. Per-tick L extraction requires
the swap-level pipeline (M1) — explicitly listed in proposal §5 Step 2.

---

## 4. No JIT filter

**Objection.** Just-in-time liquidity provision (open and close in one block)
inflates fee revenue without bearing real LVR. Some Ekubo pools may have
significant JIT activity; mixing JIT and passive LPs in the wedge is a
type-I error.

**Status: M1 deliverable.** `compute/lp_simulator.py` is set up to evaluate
profile-specific P&L. Cohort tagging — separating JIT from passive positions
by holding-period heuristic — is M2's Step 6 (`Cohort and strategy tagging`).
Today's daily-aggregate API cannot distinguish.

---

## 5. Funding-rate cost on the hedge is unmodeled

**Objection.** Section 3.3 of the proposal claims Extended Exchange has
continuous funding accrual but never measures it. A delta-neutral vault that
ignores funding cost can lose money even when the wedge is favorable.

**Status: Addressed (skeleton).** `scripts/pull_extended_funding.py` pulls
Extended's funding history (with placeholder fallback when geo-restricted).
`compute/lp_simulator.py::simulate` accepts `funding_rate_per_year` and
charges funding on the time-weighted hedge notional. Empirically, the demo
runs with `funding=0` since Extended's API is geo-blocked from this
environment; M1 adds the proper integration.

---

## 6. STRK σ_realized of 178% over the window is regime-conditional

**Objection.** STRK was in a sharp drawdown the week of the pull. Headlining
"STRK pool wedge −154 pp" without acknowledging the regime is misleading.

**Status: Addressed in proposal narrative + analysis layer.** Proposal §1.1
explicitly notes STRK was in a high-vol regime. `analysis/cross_amm.py`
exposes per-pool wedge series; the empirical paper will use a **rolling
30-day window** for the headline H3 test rather than the 7-day scoping
window. The 7-day pull is the scoping panel; the project window will average
across regimes.

---

## 7. Statistical power claim is unaudited

**Objection.** "9.5% wedge detectable at 80% power" is asserted in proposal
§4.3 without showing the simulation that justifies it. Reviewers want to see
the calculation.

**Status: Addressed.** `figures/make_figures.py` Figure 4 builds the power
curve from a closed-form one-sided z-test; the parameters that move the
threshold (n_obs, ρ, α) are explicit. A reproducible Monte-Carlo power
sim is a small extension — pending in `scripts/power_simulation.py` (TBD).

---

## 8. No σ_fee term structure

**Objection.** σ_fee at one window is one number. Real microstructure work
shows the **σ_fee term structure** (1min / 5min / 1h / 1day windows) — its
slope tells you whether the AMM is over-pricing short-term or long-term vol.

**Status: M2 deliverable.** Listed under proposal §5 Step 5, which defines
the σ_fee solver "over a 1-minute integrated window, conditional on
accumulator state φ_t" — but the swap-level data is required to fill in
the short-window end of the curve. The compute kernel
`sigma_fee_solve` in `compute/sigma_fee.py` works on any window length
already; the constraint is data, not code.

---

## 9. Cross-AMM comparison is informal

**Objection.** Comparing Avalanche LB to Ekubo via "median wedge looks
similar" is not a test. There needs to be a formal cross-section regressing
wedge on AMM-design dummies.

**Status: Addressed.** `analysis/cross_amm.py::PoolPanel` provides a
unified data structure across the three AMMs; `analysis/hypothesis_tests.py`
ships Fama-MacBeth two-pass. M3's empirical paper formalizes this with
pool-level fixed effects and AMM-design indicators (sub-bp vs 1bp tick;
constant-product vs constant-sum within rung).

---

## 10. Pragma vs CEX vs Extended — oracle latency is unmeasured

**Objection.** A delta-neutral vault hedges on Extended at Pragma's TWAP
price (or its own RPC). The lag between Coinbase, Pragma, and Extended is
measurable and matters: Bouchard et al. (2026) treat exactly this.

**Status: M1 deliverable.** Pragma + Voyager pulls are itemized in the
budget. The Bouchard et al. cross-venue HJB is cited in proposal §3.3 as
the analytical scaffold. The pipeline reads Pragma feeds today
(`docs/architecture.md`), but the lag analysis is a swap-level question
not answerable from the daily aggregates.

---

## 11. The Cairo library is not actually deployable today

**Objection.** `cairo/ekubo_greeks/src/fixed_point.cairo::fp_sqrt` uses
`u256_sqrt` which loses precision at 18-decimal scale. A production library
would use Alexandria Math or `cubit`.

**Status: Addressed (acknowledged).** `cairo/ekubo_greeks/README.md` explicitly
states "Do not use this library for funds-bearing logic until v1.0
(audit complete)." v1.0 is M3's deliverable, after a Nethermind / Cairo
Security Clan light review.

---

## 12. No formal LVR identification — fees ≠ LVR by construction

**Objection.** "wedge = fee_yield − σ" is a heuristic. The Milionis
formulation is: under no-arbitrage, expected LVR over a window equals the
σ²·∫ℓ(p)p dt term, and you compare to *actual fees from arbitrageurs only*
(not all fees). Fee revenue contains both informed and uninformed flow.

**Status: M2 deliverable.** Proposal §5 Step 5 splits LVR into "tick-crossing
LVR" (the truly informed component) and "within-tick LVR." Step 4's per-fill
markouts separate informed from uninformed flow. With those components, the
wedge becomes: σ_fee_aware = √(4 · uninformed_fees / (L·√p·dt)), which is the
correct test. Today's wedge is the all-fee proxy and will be revised.

---

## 13. No clean fixed-effects panel

**Objection.** The hypothesis tests need pool fixed effects to absorb
time-invariant pool quality differences (depth, age, contract version).

**Status: Addressed (stub).** `analysis/hypothesis_tests.py::fama_macbeth`
is the canonical cross-section. Pool fixed effects via dummy-variable
extension are trivial in numpy; M3's `cross_amm.py` adds them as a
convenience method (TBD: `PoolPanel.with_fixed_effects()`).

---

## 14. Bootstrap CI doesn't account for cross-pool correlation

**Objection.** Block bootstrap on a 1D series misses the cross-section.
Pools in the same sector (BTC vs ETH vs STRK) move together; the 14 pools
are not 14 independent draws.

**Status: M3 deliverable.** Today's bootstrap is per-series; the proper
multivariate bootstrap (cluster bootstrap by sector) is a one-liner
extension — listed as TBD in `compute/bootstrap.py`. M3's paper will use
sector-clustered SEs.

---

## 15. The whole framework assumes σ is exogenous

**Objection.** σ_realized is computed *from* the price process the AMM also
participates in. There's an endogeneity loop: AMM activity affects price
discovery, which affects σ_realized. The Milionis framework assumes σ is
exogenous (CEX-driven); for a Starknet-dominant pair (e.g., EKUBO/ETH where
Ekubo *is* the price), this assumption breaks.

**Status: Mitigated; flagged in §6 of the empirical paper outline.** Our
target pools (USDC/USDT, USDC/ETH, USDC/STRK, USDC/WBTC) all have deep
external CEX markets; σ_realized is plausibly exogenous. EKUBO/ETH and
xSTRK/STRK are *not* in the H1-H4 test set for exactly this reason. They
appear in the scoping panel for completeness but are flagged as
endogenously-priced.

---

## What's still genuinely missing

Two pieces an elite reviewer will catch that we don't yet address:

1. **No Kyle-type price-impact regression** to separately identify informed-
   flow toxicity per pool. Standard for any microstructure paper. M3
   addition.

2. **No Bayesian posterior on the wedge** — a frequentist bootstrap CI is
   adequate but a hierarchical model with sector-level partial pooling
   would extract more information from 14 pools. Stretch goal.
