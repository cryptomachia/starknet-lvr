//! Public interfaces — depositor, hedger, observer surfaces.

use starknet::ContractAddress;

#[starknet::interface]
pub trait ILvrLabVault<TContractState> {
    /// Deposit `amount_usdc` (in 6-dec USDC wei) and receive vault shares.
    /// Returns the number of shares minted.
    fn deposit(ref self: TContractState, amount_usdc_wei: u256) -> u256;

    /// Burn `shares` and receive proportional USDC + (locked) ETH equivalent.
    /// Returns (usdc_returned, eth_returned).
    fn withdraw(ref self: TContractState, shares: u256) -> (u256, u256);

    /// Read current vault NAV in USDC numéraire.
    fn nav_usdc(self: @TContractState) -> u256;

    /// Read the current LP position state.
    fn position_state(self: @TContractState) -> u8;   // 0=below, 1=in, 2=above range

    /// Read current Δ in token0 (ETH) units. Off-chain hedger uses this.
    fn current_delta_eth(self: @TContractState) -> u256;

    /// Get pool metadata.
    fn pool_id(self: @TContractState) -> felt252;
    fn share_price_usdc(self: @TContractState) -> u256;

    // ---------- Admin (testnet curator) ----------
    fn rebalance_band(ref self: TContractState, new_p_a: u256, new_p_b: u256);
    fn set_hedge_trigger_threshold(ref self: TContractState, new_threshold_bps: u256);
}


#[starknet::interface]
pub trait IHedgerOperator<TContractState> {
    /// Called by the off-chain hedger after it has placed/canceled perp orders.
    /// Records the latest hedge state on-chain for transparency.
    fn report_hedge_state(
        ref self: TContractState,
        short_size_eth_wei: u256,
        venue: felt252,
        timestamp: u64,
    );

    /// Returns the latest hedge state reported by the operator.
    fn get_hedge_state(self: @TContractState) -> HedgeState;
}


#[derive(Copy, Drop, Debug, PartialEq, Serde, starknet::Store)]
pub struct HedgeState {
    pub short_size_eth_wei: u256,
    pub venue: felt252,
    pub last_reported_at: u64,
}
