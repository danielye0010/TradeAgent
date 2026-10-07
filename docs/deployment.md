# Execution boundary

The default v0.2 operations are `init`, `scan`, `resolve`, `learn-daily`,
`evolve-weekly`, `inspect`, `retire` and `demo`. They operate on market inputs and
experience state without broker placement. No signing keys, deployment manifest,
broker account, funded account or live canary is needed for the RSI loop.

Scheduling and production market collection remain external. No production schedule,
daemon or automatic source-editing agent is installed. Prospective input collection
must preserve actual information availability times.

## Preserved v0.1 substrate

The complete pre-refactor baseline is tagged `v0.1-execution-core`. Broker normalization,
official contracts, account/risk boundaries, durable intent, submission references,
ambiguous-submission recovery, accurate fill persistence, process locking and fenced
leases are retained and tested.

Legacy signed policy, enrolled verification keys, deployment hashes and canary/
autonomous wrappers are isolated in `tradeagent.legacy`. Their controls continue to
fail closed. They are compatibility infrastructure, not the research operating model.
The legacy manifest covers current source bytes for those optional library checks;
the normal project checker verifies provenance and documentation without requiring
signed deployment artifacts. No keys were enrolled and no real orders were submitted.

The old final real-money canary is canceled. `canary-buy`, `canary-exit` and `run-once`
are absent from the product CLI. The preserved legacy standalone CLI is a library
interface for separately authorized future execution; it is not scheduled or called
by any research operation.

## Future live calibration

Research TradePlans do not automatically submit orders. A future bridge must size
within deterministic capital/instrument bounds and pass plans through the existing
durable lifecycle; it must preserve intent-before-I/O, duplicate protection and
no blind retry. That bridge and new live authorization model are deferred.

Sparse real fills should calibrate execution quality. The experience schema includes
live-expression records, independent of alpha observations. `import-execution --input`
imports externally reconciled records and appends separate fill attributions even
after alpha attribution exists. No automatic broker-fill reader is implemented.

## State and recovery

Research uses `experience.sqlite3`; execution retains `state.sqlite3`, journal and
leases. Never reinterpret execution history as prospective predictions or overwrite
a DB to clear uncertainty. Research initializes a new versioned DB and refuses
unknown schemas. Use new local directories for development and demos.

After an interrupted execution submission, preserve the journal and reconcile the
persisted reference against the broker. Match fills, fees, positions and cash before
new entries. Never automatically retry an ambiguous result. Acknowledgement is not
a fill. The legacy lease may outlive its worker; wait for normal expiry.
