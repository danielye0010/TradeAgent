# Correctness and experiment integrity

The important boundaries are durable intent before submission, no blind retry after
ambiguous submission, duplicate prevention, broker readback, accurate fill persistence,
crash recovery and deterministic account/capital/instrument limits. Existing execution
tests continue to verify them. See [Execution boundary](deployment.md).

Research adds immutable forecasts and versions, decision-time information cutoffs,
separate future observations, exact-horizon idempotent resolution, append-only
outcomes/lessons/evaluations and explicit promotion records. Synthetic/replay pools
cannot promote. Fixed evaluation rules precede the prospective evidence. Losing
experiments remain in the DB. See [Research protocol](research-protocol.md).

Store runtime SQLite, locks, journals and lease files on persistent local Linux
storage, outside Git. Keep credentials and private keys external. Tests and demos
use new synthetic directories and mocked transports. The default research workflow
does not request broker authentication or place, review or cancel real orders.
