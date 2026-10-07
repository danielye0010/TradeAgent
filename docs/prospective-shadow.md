# Prospective shadow operation

The prospective worker has no broker/account/order client. It collects QQQ and IWM
against SPY from the fixed Alpaca SIP market-data endpoint and records immutable
predictions and one-hour outcomes. It never reviews, places or cancels broker orders.

## Time and data contract

The decision is **09:33 America/New_York** on an eligible XNYS session, starting
October 8, 2026. At a typical decision the signal uses the two opening minutes,
ending 09:31 and 09:32, actually received before 09:33. The provider minute timestamp
is its start; completion is start + 60 seconds; availability is actual local receipt.
Alpaca emits minute bars after the minute boundary, so completion and availability
cannot be treated as the same instant.
[Provider timing documentation](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).

The scheduler may persist the frozen snapshot from decision time through decision +
30 seconds. Creation time is separately recorded; this window never admits a bar,
quote or reference received after the decision. There must be at least two contiguous
opening bars with aligned symbol/benchmark ends and a final-bar age at most 120
seconds. Quotes must also be fresh. A missed window or unavailable frozen input
records one skip, with no replay/backfill. Normal fractional polling times work.

The horizon remains exactly 3600 seconds after decision. The resolver needs
contiguous symbol AND benchmark observations from 09:33 through 10:33, actually
received before resolution. For delayed signal bars it measures returns from the
09:33 bar open, not the old 09:32 signal close. The signal remains prior information;
the decision entry open is later outcome evidence, never a strategy input.
Missing minutes remain unresolved. First-observed bar facts never change; revised
numeric source facts remain in separate receipts.

XNYS sessions exclude holidays/weekends and bound regular-session collection;
New York timezone rules handle DST. Whole-machine suspension may miss a decision.
Historical inputs and replay databases never enter prospective collection.

## Ownership and state

One systemd user unit, `tradeagent-prospective.service`, owns the loop. Its lifetime
`experience.lock` is acquired before deployment/database writes and also excludes
one-shot research commands using that state. Two-symbol snapshots commit together.
A crash before the separate scheduler receipt is recovered by prediction identities,
without duplicated rows. Interrupted lock files do not need deletion.

The current-user Windows task `TradeAgentProspectiveWSL` only holds WSL alive.
Task Scheduler suppresses overlap; a Linux `flock` additionally prevents orphaned
wrapper restarts from creating extra lifetime anchors. It never starts research
or execution itself.
[Microsoft explains why WSL needs lifetime support](https://learn.microsoft.com/en-us/windows/wsl/systemd).

The revised source/configuration uses `work/prospective-v2`, a fresh cold Linux
state directory. The original `work/prospective` state is preserved unchanged.
Existing deployment markers refuse changed configurations instead of silently
mixing decision contracts or outcomes. No counters are backfilled.

`experience.sqlite3` owns predictions/outcomes; `collection.sqlite3` owns normalized
receipt facts and scheduler steps. Only due unresolved predictions trigger resolution;
resolved history is not repeatedly imported on every poll.

Daily statistics run after outcomes commit. Learner failure is recorded and leaves
core collection intact; explicitly rerun learning to repair failed research work.
The unattended collector does not generate or evaluate challengers or change champion
pointers. Use explicit `evolve-weekly`/retirement commands and frozen evaluation rules
in an operator-controlled research window. No parameters or learner formulas were tuned.

Status has one mode, SHADOW, and separate health/blocks. Reports run every 30 seconds;
status-file I/O failure does not undo predictions or stop market collection.
SQLite/integrity uncertainty and unknown evidence sources still halt.

## Installation and credentials

In an interactive WSL terminal:

```bash
.venv/bin/python -m tradeagent.prospective.access
.venv/bin/python scripts/install_shadow.py
```

Hidden setup uses `getpass` and aborts without echo suppression. systemd encrypts
the credential outside the repository; the worker reads only its private runtime
credential through `LoadCredentialEncrypted`. No plaintext persistent copy,
credentials in argv/environment or interactive fallback is introduced. Missing
credentials fail closed with a sanitized blocked status. Entitlement/connectivity
cannot be inferred from a credential's existence.

The installer defaults to fresh `work/prospective-v2`; an explicit `--state-dir`
may select another compatible local state directory. It reuses the same unit name,
never installs a second trading owner. Enable user lingering administratively.
Run `scripts/install_shadow_host.ps1` on Windows for the lifetime task. No Windows
password or execution-policy change is needed. RemoteSigned may reject invoking
the unsigned installer directly from a WSL network path; the same existing-task
action can be configured with native Task Scheduler commands.

Redirects are rejected. Only whitelisted numeric data facts are persisted; vendor
error bodies/headers and credential-bearing tracebacks are excluded. Core dumps are
disabled. The former recursive credential/report/journal scan is no longer a runtime
prerequisite; the credential comparison helper remains available for explicit diagnostics.

## Safe checks and outstanding validation

```bash
systemctl --user status tradeagent-prospective.service
cat work/prospective-v2/STATUS.md
tradeagent demo --demo-dir work/new-shadow-smoke
tradeagent execution simulate --demo-dir work/new-execution-smoke
```

Use new synthetic directories. Neither demo supplies prospective evidence. Runtime
status includes heartbeat, latest collection, decision/prediction/resolution times,
counts and explicit zero broker reads/reviews/placements/cancellations.

Authenticated baseline and post-refactor live smoke collection are blocked when the
encrypted market-data credential is absent. After operator setup, check fresh receipt
timestamps and the first eligible shadow capture before interpreting any results.
No profitability, SIP entitlement, live fill behavior or physical sleep/power recovery
is established by synthetic tests.

Stop with `systemctl --user disable --now tradeagent-prospective.service` and disable
the named Windows task. Preserve state and encrypted credentials for recovery.

Optional learned-selector metadata failure preserves valid shadow predictions and
records an unavailable-selection rationale with NO_TRADE plans. Strategy identity,
snapshot validity and SQLite errors still fail closed. This fallback does not create
a substitute selected trading strategy.
