//! Vault events — emitted on every depositor/hedger interaction.

use starknet::ContractAddress;

#[derive(Drop, starknet::Event)]
pub struct Deposit {
    #[key]
    pub depositor: ContractAddress,
    pub amount_usdc_wei: u256,
    pub shares_minted: u256,
    pub price_at_deposit: u256,
    pub nav_pre_usdc: u256,
    pub nav_post_usdc: u256,
}

#[derive(Drop, starknet::Event)]
pub struct Withdraw {
    #[key]
    pub depositor: ContractAddress,
    pub shares_burned: u256,
    pub usdc_returned: u256,
    pub eth_returned: u256,
    pub price_at_withdraw: u256,
}

#[derive(Drop, starknet::Event)]
pub struct PositionRebalanced {
    pub old_p_a: u256,
    pub old_p_b: u256,
    pub new_p_a: u256,
    pub new_p_b: u256,
    pub liquidity_after: u256,
}

/// THE event the off-chain hedger subscribes to.
/// Emitted whenever the LP's δ_token0 has drifted by more than the trigger threshold.
#[derive(Drop, starknet::Event)]
pub struct HedgeTriggerEvent {
    #[key]
    pub pool_id: felt252,
    pub previous_target_short_eth_wei: u256,
    pub new_target_short_eth_wei: u256,
    pub current_pool_price: u256,
    pub current_lvr_rate_per_year_usdc: u256,
    pub block_number: u64,
}

#[derive(Drop, starknet::Event)]
pub struct HedgeStateReported {
    #[key]
    pub operator: ContractAddress,
    pub short_size_eth_wei: u256,
    pub venue: felt252,
    pub at_block: u64,
}

#[derive(Drop, starknet::Event)]
pub struct ParameterUpdated {
    #[key]
    pub param_name: felt252,
    pub old_value: u256,
    pub new_value: u256,
}
