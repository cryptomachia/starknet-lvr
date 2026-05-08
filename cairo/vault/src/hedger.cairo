//! Hedger operator helper — separates hedge-state reporting from the vault.
//!
//! The off-chain hedger bot signs transactions to call `report_hedge_state`
//! after placing/canceling perp orders on Extended. This contract receives
//! those reports and exposes them to the dashboard.
//!
//! In v0.1, this is a thin shim over the same vault state. Future v1.0
//! splits it into a separate contract for cleaner permissions.

#[starknet::contract]
pub mod HedgerOperator {
    use starknet::{ContractAddress, get_caller_address, get_block_timestamp};
    use starknet::storage::{StoragePointerReadAccess, StoragePointerWriteAccess};
    use super::super::interfaces::{IHedgerOperator, HedgeState};
    use super::super::events::HedgeStateReported;

    #[storage]
    pub struct Storage {
        pub vault: ContractAddress,
        pub authorized_operator: ContractAddress,
        pub hedge_state: HedgeState,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        HedgeStateReported: HedgeStateReported,
    }

    #[constructor]
    fn constructor(
        ref self: ContractState,
        vault: ContractAddress,
        operator: ContractAddress,
    ) {
        self.vault.write(vault);
        self.authorized_operator.write(operator);
    }

    #[abi(embed_v0)]
    impl HedgerOperatorImpl of IHedgerOperator<ContractState> {
        fn report_hedge_state(
            ref self: ContractState,
            short_size_eth_wei: u256,
            venue: felt252,
            timestamp: u64,
        ) {
            let caller = get_caller_address();
            assert(caller == self.authorized_operator.read(), 'unauthorized');

            let now_block = get_block_timestamp();
            let new_state = HedgeState {
                short_size_eth_wei,
                venue,
                last_reported_at: timestamp,
            };
            self.hedge_state.write(new_state);

            self.emit(Event::HedgeStateReported(HedgeStateReported {
                operator: caller,
                short_size_eth_wei,
                venue,
                at_block: now_block,
            }));
        }

        fn get_hedge_state(self: @ContractState) -> HedgeState {
            self.hedge_state.read()
        }
    }
}
