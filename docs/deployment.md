# Deployment

TradeBot runs once per invocation. Its current broker integration uses Robinhood's official Trading MCP.

## Modes

| Mode | Operation |
| --- | --- |
| Simulation | Synthetic equity and option orders with local journals. |
| Shadow | Real account and market reads; no order review, submission, or cancellation. |
| Supervised | Equity orders bound to exact external approval. |
| Canary | Equity execution within a small signed account, quantity, and economic limit. |
| Limited autonomous equity | Repeated execution within a signed policy's limits. |

The default configuration is SHADOW. Production verification keys are not enrolled, and the deployed autonomous phase is CANARY. Live options and scheduled unattended operation are not supported.

## Shadow mode

Set up the [official MCP connection](getting-started.md#connect-robinhood), then run:

```bash
tradebot shadow
tradebot inspect
```

Each invocation runs once and records its decisions in the configured state directory.

## Real equity execution

Real execution requires:

- An eligible account and compatible authenticated MCP connection
- Current instrument classification, market data, and corporate-action inputs
- A deployment manifest matching the installed files
- A human-owned public verification key enrolled in the production code
- An externally signed approval or policy covering the account, deployment, strategy, config, risk settings, instruments, and permitted phase
- Persistent state, order monitoring, and restart/reconciliation handling

Keep the private signing key outside the repository and runtime. Policies have expiry, quantity, session, and economic limits. Deployed file changes require a refreshed manifest and matching signature. The public policy template contains placeholder fields and needs completed context and an external signature.

Configure and test account-specific deployment before enabling real execution. Advance from canary to broader equity operation with its own configuration, instrument coverage, and signed limits. The included strategy has no established live performance record.

## Standalone mode

`run-once`, `canary-buy`, and `canary-exit` use the direct MCP client. Supply `--oauth-helper` with an absolute path to an owner-only local executable outside the checkout. The helper handles OAuth login and refresh. It must return a token, expiry, and official resource to the client; tokens are held in memory.

Real mode also requires `--policy`, `--public-key`, and `--universe-evidence`. The helper is supplied by the operator. Direct authenticated connection and account compatibility need to be checked for that deployment. The project does not install a scheduler or service.

## State and recovery

Store real state on persistent local Linux storage. The default directory is `data/`; use separate directories for simulation and real accounts. Backups should protect account and order information and stay out of Git.

After an interrupted submission, stop new entries and preserve the journal. Match the broker order to its persisted submission reference, then reconcile fills, fees, cash, positions, and open orders. Do not retry, reprice, cancel, reset allowances, or replace approval while the result is unresolved.

A durable lease can outlive the worker's OS lock. Wait for normal expiry and check ownership rather than deleting it. Unresolved identity, unexplained cash movement, mismatched fills, expired policy, or approval-required receipts need operator handling. Exits outside a signed policy require new authorization.
