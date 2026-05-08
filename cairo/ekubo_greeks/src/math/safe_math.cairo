//! Saturation- and overflow-safe arithmetic helpers.
//!
//! Cairo's u256 traps on overflow by default; these helpers turn that into
//! either explicit saturation or `Result`-typed returns for non-panicking
//! call sites.

use core::result::Result;

#[derive(Drop, Debug, PartialEq)]
pub enum MathError {
    Overflow,
    Underflow,
    DivByZero,
    DomainError,
}

/// Checked addition.
pub fn checked_add(a: u256, b: u256) -> Result<u256, MathError> {
    let max = 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff_u256;
    if a > max - b {
        Result::Err(MathError::Overflow)
    } else {
        Result::Ok(a + b)
    }
}

/// Checked subtraction.
pub fn checked_sub(a: u256, b: u256) -> Result<u256, MathError> {
    if a < b {
        Result::Err(MathError::Underflow)
    } else {
        Result::Ok(a - b)
    }
}

/// Checked division.
pub fn checked_div(a: u256, b: u256) -> Result<u256, MathError> {
    if b == 0_u256 {
        Result::Err(MathError::DivByZero)
    } else {
        Result::Ok(a / b)
    }
}

/// Min / max for u256.
pub fn min_u256(a: u256, b: u256) -> u256 {
    if a < b { a } else { b }
}

pub fn max_u256(a: u256, b: u256) -> u256 {
    if a > b { a } else { b }
}

/// Absolute difference.
pub fn abs_diff(a: u256, b: u256) -> u256 {
    if a >= b { a - b } else { b - a }
}
