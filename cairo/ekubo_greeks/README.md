# `ekubo-greeks`

Cairo library for v3-style concentrated-liquidity position math: Greeks (Δ, Γ),
impermanent loss, and instantaneous loss-versus-rebalancing (LVR).

Mirrors the Python reference implementation in `src/lvr_lab/compute/`.
Both are anchored to closed-form derivations from Lambert (2021) and
Milionis-Moallemi-Roughgarden-Zhang (2022).

## Status (v0.1)

- ✅ Core Greeks: `position_amounts`, `position_value`, `delta_token0`, `gamma`
- ✅ Marginal liquidity ℓ(p) and LVR rate (in-range)
- ✅ Impermanent loss vs HODL
- ✅ Cairo tests with cross-validated anchor points
- ❌ Time-integrated LVR — pending; trapezoidal integration over a price path
- ❌ Full-range piecewise gamma sign handling — currently returns absolute value
- ❌ Production-grade fixed-point math — current `fp_sqrt` uses core `u256_sqrt`
  which loses precision at the extremes. M3 swap-in: Alexandria Math or `cubit`.

**Do not use this library for funds-bearing logic until v1.0 (audit complete).**

## Build / test

```bash
scarb build
scarb cairo-test
```

Requires Scarb 2.8.0+ and a recent Cairo toolchain.

## Conventions

- 18-decimal fixed-point on `u256` for all prices and amounts.
- Token1 numéraire (e.g., USDC for an ETH/USDC pool).
- σ enters in the same time-unit basis as the desired LVR rate.
- All functions are pure / view.

## Why this exists

Every future Starknet vault, ALMM strategy, or analytics product that touches
Ekubo positions needs these primitives. Currently the only on-chain reference
is Ekubo's oracle extension, which is unaudited and only exposes price (not
position state). `ekubo-greeks` fills the dependency gap — it is the
foundational layer the rest of the SNF Seed Grant proposal builds on.

## Citation

If you use this library:

```
Huang, E. (2026). ekubo-greeks: Cairo library for Ekubo concentrated-liquidity
position math. SNF Seed Grant, Starknet Foundation.
```
