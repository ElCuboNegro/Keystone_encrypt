---
id: ADR-D003
title: Password mismatch -- backoff + no oracle leakage
status: Accepted
date: 2026-03-11
---

# ADR-D003: Password mismatch — backoff + no oracle leakage

## Status
Accepted

## Context
An attacker with physical access to the vault files could run offline brute-force attacks.
The GUI unlock flow must not assist such attacks by leaking which factor (password vs card) is wrong,
nor by returning faster on incorrect inputs.

## Decision
Apply the following policy (defined in `skills/security-expert.md`):

1. **No oracle leakage**: PBKDF2 always runs to completion. The error message is always
   "Wrong password or wrong card." regardless of which factor failed.
2. **Exponential backoff**: After each failure, the vault is locked out for `min(2^(n-1), 64)` seconds.
3. **Hard lockout**: After 10 attempts, the vault is locked for 15 minutes.
4. **Persistence**: Attempt counter and lockout timestamp are saved to `~/.keystone/attempts.json`
   and survive process restarts.

## Justification
- Full PBKDF2 execution prevents timing side-channel distinguishing wrong-password from wrong-card
- Backoff defeats online brute-force even if attacker can restart the process
- Persisted counter survives kill -9 and power cycles — cannot be reset by restarting

## Consequences
- Legitimate users who forget their password face increasing wait times
- The `~/.keystone/attempts.json` file is not encrypted (acceptable: it contains no secrets,
  only a counter and a timestamp)
- An admin can manually delete the file to reset the counter
