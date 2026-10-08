# One-shot execution validation

The packaged CLI can execute an isolated paper entry-and-exit cycle without an
LLM process. It reuses the existing durable order engine, simulator, risk checks,
process lock, fill persistence and cancellation/reconciliation paths. It never
opens the prospective research database or consumes a research strategy signal.

The production one-shot path is incomplete. `run-once --live` reports
`LIVE BLOCKED` before constructing a broker or creating execution state. It is
not an owner-ready LIVE launch command.

## Install independently on Linux

Build from the source checkout, then install in a separate environment:

```bash
python3 -m venv .build-venv
.build-venv/bin/pip install build
.build-venv/bin/python -m build
python3 -m venv .execution-venv
.execution-venv/bin/pip install dist/tradeagent-0.2.0-py3-none-any.whl
.execution-venv/bin/tradeagent run-once --paper --state-dir work/fresh-one-shot
```

Keep all execution state on persistent local Linux storage. Use a distinct
checkout/environment and state directory from any running prospective service.
The installed wheel needs neither the source checkout nor Codex for paper
execution. No service or timer is installed by these commands.

## Paper policy and reports

The builtin fixture is labeled `SYNTHETIC_EXECUTION_CANARY`: prices of $10 for
IWM and $12 for QQQ are invented software-test inputs. They are not current or
historical market observations. The virtual clock starts at 2026-10-05 14:00 UTC;
it advances without waiting in real time. These reports are excluded from
strategy performance and are not evidence of alpha or production execution.

The runner deterministically selects an eligible unowned symbol, sizes whole
shares using available cash, buying power and unchanged risk limits, and caps
entry notional at $25. Insufficient capital returns `NO_TRADE`. Default holding
time is 3,600 virtual seconds, capped at ten minutes before the regular session
close. The runner permits at most one entry and one corresponding exit.

The core requires a review authorization. Paper execution uses its existing
explicitly simulated authorization fixture. This is not a real standing grant.

```bash
tradeagent run-once --paper --state-dir work/fresh-one-shot --max-notional 25 --hold-seconds 3600
tradeagent run-once --paper --state-dir work/fresh-one-shot --max-notional 25 --hold-seconds 3600
```

The second invocation reconciles the same run without reentry. Options and the
initial fixture are bound to a durable marker; changing them requires a new
paper directory. A foreign nonempty directory is rejected.

Each directory contains `report.json`, `paper-run.json`, the existing engine's
`agent/` SQLite state and exported journal, and separate `broker/` simulator
state. The report records normalized orders, actual simulated executions, known
fees, cash, positions, decision/submission times, exit reason and realized P&L
only when both sides fully close. A partial exit records the residual and an
incident; no second exit is silently submitted. `HALTED` is not successful
reconciliation. `NO_TRADE` does not imply a profitable signal was found.

Known pending orders get bounded reconciliation and one permitted simulated
cancellation. Ambiguous submissions are read/reconciled by durable reference;
they are never resubmitted. A restart after an accepted order can cancel its
unfilled remainder. A restart after a confirmed entry fill can exit that owned
quantity without another buy. An unknown absent order remains an incident.

Create `KILL` inside the run directory (or use `--kill-switch /absolute/path`)
to prohibit entry. A recovered filled position may still use its single allowed
risk-reducing exit. This local file and the exported high-priority incident event
are operator controls; no remote notification channel has been configured.
Paper fault scenarios are available through `--entry-scenario` and
`--exit-scenario`; use `tradeagent run-once --help` for the existing simulator
choices. Exit status is 0 for `COMPLETED`/`NO_TRADE`, and 2 for `HALTED`.

## Read-only Robinhood check

```bash
tradeagent live-check --oauth-helper /absolute/external/oauth-helper \
  --root /absolute/source-checkout --output work/live-check/report.json
```

The external owner-only helper supplies resource-bound OAuth tokens and owns
refresh outside the repository. See [Prospective operation](prospective-shadow.md)
for its contract. `TRADEAGENT_OAUTH_HELPER` can supply the helper path. Credentials
are never written to the report. Do not commit reports containing account state.

The direct HTTP preflight checks the frozen read schemas. Its transport rejects
review, placement, cancellation and write authorization before token refresh or
HTTP. It separately reports write-catalog drift without updating production
pins. It performs at most two account snapshots when quotes and depth disagree;
all other uncertainty stops immediately. Successful authentication is distinct
from account verification and LIVE readiness. The current readiness command
returns exit status 2 even if authenticated reads succeed.

## Production prerequisites

These are unresolved requirements, not authorization granted by installing a
wheel or setting a CLI flag:

- LIVE mode is disabled in `model.py`.
- The existing autonomous release has no enrolled production public-key pin or
  valid owner/account/deployment-bound signed standing grant. No key, signature
  or broker permission is synthesized by this command.
- Current broker write schemas must match reviewed, pinned production contracts.
  Catalog discovery does not exercise a review or prove a successful order.
- A production controller must bind sizing, its bounded exit/cancellation policy,
  monitoring and restart recovery to the standing grant and real adapter. The
  legacy canary instead requires a later-session exit and prohibits cancellation.
  The new paper controller deliberately rejects real transports.
- Current account eligibility, funding, quote/depth agreement, broker trade
  approval settings and exceptional approval handling must be verified. Account
  data rejected by normalization cannot establish readiness.
- OAuth renewal over token expiry, credential revocation, durable host availability
  and incident delivery need operational commissioning. One authenticated check
  does not prove long-term renewal. WSL requires the Windows host to stay available.

Robinhood supports eligible external-agent orders without per-order confirmation
when its trade-approval setting is off, while some trades may still require
approval. See [Robinhood's trade approval documentation](https://robinhood.com/us/en/support/articles/trading-with-your-agent/).
The preflight does not change or claim to verify that setting. Application-level
signed authorization and broker-required approvals remain separate checks.

No live launch instructions are provided while these prerequisites remain open.
No recurring trading service or timer is installed or enabled by this feature.