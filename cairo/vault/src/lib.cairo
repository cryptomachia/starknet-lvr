//! lvr_lab_vault — Reference delta-neutral LP vault for Ekubo.
//!
//! TESTNET ONLY. This contract:
//!   1. Accepts USDC deposits from users
//!   2. Opens a single Ekubo USDC/ETH LP position with the deposit
//!   3. Tracks position delta on-chain via the `ekubo-greeks` library
//!   4. Emits `HedgeTriggerEvent` when delta drift exceeds a threshold
//!   5. Allows withdrawal back to USDC at any time (closes proportional LP)
//!
//! What this contract does NOT do (intentional scope cuts for v0.1):
//!   - Open / hedge on a perp venue   (off-chain hedger bot does this)
//!   - Multi-position management       (one position per vault)
//!   - Mainnet deployment              (testnet only; no funds at risk)
//!   - Audit-bearing security review   (light Nethermind/CSC review at v1.0)
//!   - Performance fees / curator fees
//!
//! M3 deliverable. Light third-party security review before any mainnet move.

pub mod interfaces;
pub mod vault;
pub mod hedger;
pub mod events;
