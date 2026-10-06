# Safety

## Risk checks

Every order is checked against account eligibility, unleveraged cash, cash reserves, exposure, concentration, turnover, loss limits, exchange hours, spread, and data freshness. Strategy output is a proposal; the risk checks decide whether it can be executed. Missing or inconsistent broker data stops the run.

## Duplicate orders

Stable decision identifiers and unique submission references prevent a repeated decision from creating another order. Signed canary allowances are recorded before submission and remain consumed after a restart.

## Interrupted submissions

If a submission times out after the request may have reached the broker, TradeBot records it as unresolved and reconciles the existing broker order. It does not automatically replay the request. Acknowledgements, partial fills, and final fills are handled separately.

## Runtime state

Keep SQLite, the JSONL export, process lock, and fenced lease on persistent local Linux storage. Preserve them after interruptions. Do not delete state or shorten a lease to bypass an unresolved order. Use separate directories for demos and real-account runs.

## Credentials

Codex manages native authentication. Standalone mode uses an operator-owned OAuth helper outside the checkout. Keep tokens, private signing keys, signed account artifacts, session caches, and runtime data out of Git. See [Security](../SECURITY.md) for reporting.

## Live execution

Real equity execution requires an enrolled public verification key and a valid signed approval or policy. Signed limits bind the account, deployed code, configuration, risk settings, and instruments. Simulation approvals cannot authorize real orders.

A one-share canary uses its bounded corporate-action checks; limited autonomous equity requires broader instrument coverage. Live options are not supported. See [Deployment](deployment.md) for setup and recovery.
