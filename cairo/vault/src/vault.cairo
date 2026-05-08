//! lvr_lab_vault::vault — The main contract.
//!
//! Holds an Ekubo USDC/ETH LP position. Tracks delta on-chain via the
//! `ekubo_greeks` library. Emits `HedgeTriggerEvent` when delta drifts past
//! a configured threshold, so an off-chain hedger bot knows to rebalance
//! its perp short on Extended.
//!
//! Share accounting:
//!   First depositor: shares = USDC deposited (1:1 mint at p_0)
//!   Subsequent:      shares = (deposit_usdc / nav_usdc) * total_shares
//!
//! Withdraw:
//!   shares_burned / total_shares fraction of the pool position is closed.
//!   Returns (usdc, eth) proportional to current x/y mix.

#[starknet::contract]
pub mod LvrLabVault {
    use starknet::{
        ContractAddress, get_caller_address, get_block_number, get_block_timestamp,
        get_contract_address, contract_address_const,
    };
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess,
        StoragePointerReadAccess, StoragePointerWriteAccess,
    };
    use core::num::traits::Zero;

    use ekubo_greeks::position::{Position, position_amounts, position_value, Amounts};
    use ekubo_greeks::greeks::delta_token0;
    use ekubo_greeks::lvr::lvr_rate_in_range_token1;
    use ekubo_greeks::math::fixed_point::{ONE_E18, fp_mul, fp_div};

    use super::super::interfaces::{ILvrLabVault, HedgeState};
    use super::super::events::{
        Deposit, Withdraw, PositionRebalanced, HedgeTriggerEvent,
        HedgeStateReported, ParameterUpdated,
    };

    // ---------- External dispatchers (production wiring) ----------
    // Minimal IERC20 — avoid an OZ git dep so `scarb build` works offline.
    // The standard Starknet IERC20 interface is stable across OZ versions.
    #[starknet::interface]
    trait IERC20<TContractState> {
        fn balance_of(self: @TContractState, account: ContractAddress) -> u256;
        fn transfer(ref self: TContractState, recipient: ContractAddress, amount: u256) -> bool;
        fn transfer_from(
            ref self: TContractState,
            sender: ContractAddress,
            recipient: ContractAddress,
            amount: u256,
        ) -> bool;
        fn approve(ref self: TContractState, spender: ContractAddress, amount: u256) -> bool;
    }

    // Minimal Ekubo Core surface — only the methods the vault calls.
    // Full ABI lives at https://docs.ekubo.org; we project just what's needed.
    #[derive(Copy, Drop, Serde)]
    struct EkuboPoolKey {
        token0: ContractAddress,
        token1: ContractAddress,
        fee: u128,
        tick_spacing: u128,
        extension: ContractAddress,
    }

    #[derive(Copy, Drop, Serde)]
    struct EkuboBounds {
        lower: i129,
        upper: i129,
    }

    #[derive(Copy, Drop, Serde)]
    struct EkuboDelta {
        amount0: i129,
        amount1: i129,
    }

    // Ekubo's i129 = signed 129-bit; Cairo type. We declare it as a struct
    // to make calldata serialization explicit; Ekubo's ABI uses the same
    // shape.
    #[derive(Copy, Drop, Serde)]
    struct i129 {
        mag: u128,
        sign: bool,
    }

    #[starknet::interface]
    trait IEkuboCore<TContractState> {
        /// Update / open / close a position. Returns the (signed) token deltas
        /// the LP must transfer (positive = LP pays in; negative = LP receives).
        ///
        /// Real Ekubo invokes this through a `locker` callback, not directly —
        /// the vault therefore implements its own locker by inheriting the
        /// `ILocker` interface and calling `lock` instead. This is the v0.1
        /// scaffold; M3 wires through the proper locker pattern.
        fn update_position(
            ref self: TContractState,
            pool_key: EkuboPoolKey,
            salt: felt252,
            bounds: EkuboBounds,
            liquidity_delta: i129,
        ) -> EkuboDelta;
    }

    // ---------- Storage ----------
    #[storage]
    pub struct Storage {
        // Vault metadata
        pub admin: ContractAddress,
        pub pool_id: felt252,                      // Ekubo USDC/ETH pool key hash
        pub usdc_token: ContractAddress,
        pub eth_token: ContractAddress,
        pub ekubo_core: ContractAddress,

        // Current LP position (single position; multi-position is post-v0.1)
        pub position_l: u256,
        pub position_p_a: u256,
        pub position_p_b: u256,
        pub position_opened_at_price: u256,
        pub position_opened_at_block: u64,

        // Share accounting
        pub total_shares: u256,
        pub balance_of: Map<ContractAddress, u256>,

        // Hedge tracking
        pub last_hedge_target_eth: u256,
        pub last_hedge_state: HedgeState,
        pub hedge_trigger_threshold_bps: u256,    // e.g., 500 = 5%

        // Pool oracle
        pub last_observed_price: u256,
        pub last_observed_at_block: u64,
    }

    // ---------- Events ----------
    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Deposit: Deposit,
        Withdraw: Withdraw,
        PositionRebalanced: PositionRebalanced,
        HedgeTriggerEvent: HedgeTriggerEvent,
        HedgeStateReported: HedgeStateReported,
        ParameterUpdated: ParameterUpdated,
    }

    // ---------- Constructor ----------
    #[constructor]
    fn constructor(
        ref self: ContractState,
        admin: ContractAddress,
        pool_id: felt252,
        usdc_token: ContractAddress,
        eth_token: ContractAddress,
        ekubo_core: ContractAddress,
        initial_price: u256,
        band_lower: u256,                         // p_a, e.g., 1800·10^18 for ETH
        band_upper: u256,                         // p_b, e.g., 2200·10^18
        trigger_threshold_bps: u256,              // e.g., 500 = 5%
    ) {
        self.admin.write(admin);
        self.pool_id.write(pool_id);
        self.usdc_token.write(usdc_token);
        self.eth_token.write(eth_token);
        self.ekubo_core.write(ekubo_core);
        self.position_p_a.write(band_lower);
        self.position_p_b.write(band_upper);
        self.position_l.write(0_u256);            // no LP yet; opened on first deposit
        self.position_opened_at_price.write(initial_price);
        self.last_observed_price.write(initial_price);
        self.last_observed_at_block.write(get_block_number());
        self.hedge_trigger_threshold_bps.write(trigger_threshold_bps);
    }

    // ---------- ILvrLabVault impl ----------
    #[abi(embed_v0)]
    impl LvrLabVaultImpl of ILvrLabVault<ContractState> {
        fn deposit(ref self: ContractState, amount_usdc_wei: u256) -> u256 {
            let depositor = get_caller_address();
            assert(amount_usdc_wei > 0_u256, 'deposit: zero');

            let nav_pre = self.nav_usdc();
            let total_shares = self.total_shares.read();

            // Mint shares
            let shares = if total_shares == 0_u256 {
                amount_usdc_wei
            } else {
                assert(nav_pre > 0_u256, 'deposit: NAV zero');
                fp_div(fp_mul(amount_usdc_wei, total_shares), nav_pre)
            };

            // ---- Real ERC20 transferFrom: pull USDC from depositor ----
            let usdc = IERC20Dispatcher { contract_address: self.usdc_token.read() };
            let here = get_contract_address();
            let ok = usdc.transfer_from(depositor, here, amount_usdc_wei);
            assert(ok, 'deposit: transferFrom failed');

            // ---- Real Ekubo update_position call ----
            // M3 wires the full locker callback pattern (Ekubo's `lock` →
            // callback → `update_position` → settle deltas). For the v0.1
            // testnet contract we approximate by computing the analytical L
            // and recording it; the swap-back is deferred until M3.
            //
            // The full locker pattern is documented in the Ekubo integration
            // guide and ships in M3 as part of the Cairo audit-light review.
            let p = self.last_observed_price.read();
            let new_l = self.position_l.read() + _l_from_usdc(amount_usdc_wei, p,
                                                               self.position_p_a.read(),
                                                               self.position_p_b.read());
            self.position_l.write(new_l);

            self.total_shares.write(total_shares + shares);
            let prev = self.balance_of.read(depositor);
            self.balance_of.write(depositor, prev + shares);

            self.emit(Event::Deposit(Deposit {
                depositor,
                amount_usdc_wei,
                shares_minted: shares,
                price_at_deposit: p,
                nav_pre_usdc: nav_pre,
                nav_post_usdc: nav_pre + amount_usdc_wei,
            }));

            // Check whether delta drift requires hedge update
            self._maybe_emit_hedge_trigger(p);

            shares
        }

        fn withdraw(ref self: ContractState, shares: u256) -> (u256, u256) {
            let depositor = get_caller_address();
            let bal = self.balance_of.read(depositor);
            assert(shares > 0_u256 && shares <= bal, 'withdraw: invalid shares');

            let total = self.total_shares.read();
            assert(total > 0_u256, 'withdraw: empty vault');

            // Compute the proportional position to close
            let l_full = self.position_l.read();
            let l_close = fp_div(fp_mul(l_full, shares), total);

            let p = self.last_observed_price.read();
            let pos_close = Position {
                L: l_close,
                p_a: self.position_p_a.read(),
                p_b: self.position_p_b.read(),
            };
            let amts = position_amounts(pos_close, p);

            // ---- Real ERC20 transfers back to depositor ----
            // The proportional (x_token0, y_token1) inventory amts is what
            // the Ekubo singleton would return after closing l_close from the
            // position. v0.1 transfers the analytical amounts; M3 wires the
            // locker pattern to settle Ekubo's actual returned deltas.
            let usdc = IERC20Dispatcher { contract_address: self.usdc_token.read() };
            let eth = IERC20Dispatcher { contract_address: self.eth_token.read() };
            let _ok_usdc = usdc.transfer(depositor, amts.y_token1);
            let _ok_eth = eth.transfer(depositor, amts.x_token0);

            self.position_l.write(l_full - l_close);
            self.total_shares.write(total - shares);
            self.balance_of.write(depositor, bal - shares);

            self.emit(Event::Withdraw(Withdraw {
                depositor,
                shares_burned: shares,
                usdc_returned: amts.y_token1,
                eth_returned: amts.x_token0,
                price_at_withdraw: p,
            }));

            self._maybe_emit_hedge_trigger(p);
            (amts.y_token1, amts.x_token0)
        }

        fn nav_usdc(self: @ContractState) -> u256 {
            let l = self.position_l.read();
            if l == 0_u256 {
                return 0_u256;
            }
            let pos = Position {
                L: l, p_a: self.position_p_a.read(), p_b: self.position_p_b.read()
            };
            position_value(pos, self.last_observed_price.read())
        }

        fn position_state(self: @ContractState) -> u8 {
            let p = self.last_observed_price.read();
            let p_a = self.position_p_a.read();
            let p_b = self.position_p_b.read();
            if p <= p_a { 0_u8 }
            else if p >= p_b { 2_u8 }
            else { 1_u8 }
        }

        fn current_delta_eth(self: @ContractState) -> u256 {
            let l = self.position_l.read();
            if l == 0_u256 {
                return 0_u256;
            }
            let pos = Position {
                L: l, p_a: self.position_p_a.read(), p_b: self.position_p_b.read()
            };
            delta_token0(pos, self.last_observed_price.read())
        }

        fn pool_id(self: @ContractState) -> felt252 {
            self.pool_id.read()
        }

        fn share_price_usdc(self: @ContractState) -> u256 {
            let total = self.total_shares.read();
            if total == 0_u256 {
                return ONE_E18;   // initial = 1.0
            }
            fp_div(self.nav_usdc(), total)
        }

        fn rebalance_band(ref self: ContractState, new_p_a: u256, new_p_b: u256) {
            let caller = get_caller_address();
            assert(caller == self.admin.read(), 'only admin can rebalance');
            assert(new_p_a > 0_u256 && new_p_b > new_p_a, 'invalid band');

            let old_p_a = self.position_p_a.read();
            let old_p_b = self.position_p_b.read();

            // TODO: close current LP, reopen at new band, return any leftover
            // to the share holders proportionally

            self.position_p_a.write(new_p_a);
            self.position_p_b.write(new_p_b);

            self.emit(Event::PositionRebalanced(PositionRebalanced {
                old_p_a, old_p_b, new_p_a, new_p_b,
                liquidity_after: self.position_l.read(),
            }));
        }

        fn set_hedge_trigger_threshold(ref self: ContractState, new_threshold_bps: u256) {
            let caller = get_caller_address();
            assert(caller == self.admin.read(), 'only admin');
            assert(new_threshold_bps > 0_u256 && new_threshold_bps < 10000_u256, 'threshold out of range');
            let old = self.hedge_trigger_threshold_bps.read();
            self.hedge_trigger_threshold_bps.write(new_threshold_bps);
            self.emit(Event::ParameterUpdated(ParameterUpdated {
                param_name: 'hedge_trigger_bps',
                old_value: old,
                new_value: new_threshold_bps,
            }));
        }
    }

    // ---------- Internal helpers ----------
    #[generate_trait]
    impl InternalImpl of InternalTrait {
        fn _maybe_emit_hedge_trigger(ref self: ContractState, price: u256) {
            let l = self.position_l.read();
            if l == 0_u256 {
                return;
            }
            let pos = Position { L: l, p_a: self.position_p_a.read(), p_b: self.position_p_b.read() };
            let new_target = delta_token0(pos, price);
            let prev_target = self.last_hedge_target_eth.read();
            let drift = if new_target >= prev_target { new_target - prev_target } else { prev_target - new_target };
            // Threshold: |drift / max(prev, 1)| > threshold_bps / 10000
            let threshold_fraction = self.hedge_trigger_threshold_bps.read();
            let denom = if prev_target > 0_u256 { prev_target } else { ONE_E18 };
            let drift_bps = fp_div(fp_mul(drift, 10000_u256), denom);
            if drift_bps > threshold_fraction {
                self.last_hedge_target_eth.write(new_target);
                let lvr_rate = lvr_rate_in_range_token1(pos, price, _annualized_sigma_estimate());
                self.emit(Event::HedgeTriggerEvent(HedgeTriggerEvent {
                    pool_id: self.pool_id.read(),
                    previous_target_short_eth_wei: prev_target,
                    new_target_short_eth_wei: new_target,
                    current_pool_price: price,
                    current_lvr_rate_per_year_usdc: lvr_rate,
                    block_number: get_block_number(),
                }));
            }
        }
    }

    // ---------- Free helpers ----------
    fn _annualized_sigma_estimate() -> u256 {
        // Placeholder: pull from a Pragma-backed σ feed when wired.
        // Hard-coded to 50% (5e17) until M3 wires the σ oracle adapter.
        500_000_000_000_000_000_u256
    }

    fn _l_from_usdc(amount_usdc: u256, p: u256, p_a: u256, p_b: u256) -> u256 {
        // L from V(p) = L · (2·√p − √pa − p/√pb)
        // Placeholder using the same formula as the Python reference.
        let sqrt_p = ekubo_greeks::math::fixed_point::fp_sqrt(p);
        let sqrt_pa = ekubo_greeks::math::fixed_point::fp_sqrt(p_a);
        let sqrt_pb = ekubo_greeks::math::fixed_point::fp_sqrt(p_b);
        let two_sqrt_p = ekubo_greeks::math::fixed_point::fp_mul(
            ekubo_greeks::math::fixed_point::two(), sqrt_p,
        );
        let p_over_sqrt_pb = ekubo_greeks::math::fixed_point::fp_div(p, sqrt_pb);
        if two_sqrt_p <= sqrt_pa + p_over_sqrt_pb {
            return 0_u256;
        }
        let coeff = two_sqrt_p - sqrt_pa - p_over_sqrt_pb;
        ekubo_greeks::math::fixed_point::fp_div(amount_usdc, coeff)
    }
}
