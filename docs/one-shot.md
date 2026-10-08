# Owner-operated Linux one-shot execution

TradeAgent runs independently from a Linux wheel. The owner configures the official
Robinhood connection and explicitly launches one entry with its corresponding exit.
No Codex, Claude, LLM, signing service, Ed25519 key, signed grant, prepare-once,
setup-once or authorization directory is required. Package installation,
configuration and live-check never submit orders or install a service or timer.

## Install and configure

Use Python 3.12–3.14 on Linux or Ubuntu/WSL2 and a dedicated environment, separate
from an existing SHADOW environment:

```bash
python3 -m venv ~/tradeagent-owner-venv
source ~/tradeagent-owner-venv/bin/activate
pip install /absolute/path/tradeagent-0.2.3-py3-none-any.whl
umask 077
mkdir -p ~/.config/tradeagent
cd ~/.config/tradeagent
python -c 'from importlib.resources import files; from pathlib import Path; Path("tradeagent.toml").open("x").write(files("tradeagent").joinpath("owner.example.toml").read_text())'
chmod 600 tradeagent.toml
```

Edit the packaged [owner.example.toml](../src/tradeagent/owner.example.toml) copy:
replace both `/absolute/...` placeholders with absolute paths on your local Linux
filesystem. The state path must be an isolated fresh directory, never the SHADOW
or research state path. The OAuth helper must be an existing owner-only executable
outside the package and repository. Keep access/refresh tokens, credentials and
login entirely in the external helper; the TOML accepts no credential fields.
See [helper contract](prospective-shadow.md). Authentication is never fabricated.

The complete configuration is:

```toml
[live]
enabled = true
symbols = ["QQQ", "IWM"]
state_dir = "/absolute/linux/one-shot-state"
max_notional = "25"

[broker]
oauth_helper = "/absolute/external/robinhood-mcp-oauth-helper"
timeout_seconds = 20

[risk]
max_positions = 5
max_position_fraction = "0.20"
max_new_exposure_fraction = "0.10"
min_cash_fraction = "0.20"

[entry]
order_type = "market"
dollar_amount = "5"

[exit]
order_type = "market"
hold_seconds = 30
polls = 3
session_buffer_seconds = 60
```

The owner chooses `live.symbols`; there is no QQQ/IWM/SPY whitelist. LIVE requires
current US tradability metadata and an eligible authenticated account. Fractional
entries additionally require the account-specific `fractional_tradability` result.
The example is a $5 dollar-based market buy, one entry and one exact quantity exit,
with a strict configured $25 entry ceiling. Change the explicit amount to `"10"`
for a $10 test. There is no automatic capital increase, leverage or short selling.
Risk settings still enforce position, exposure, cash reserve, spread, depth,
daily loss and turnover limits, and may reduce the available budget. The runner
rejects a requested amount that exceeds the budget; it does not silently resize it.

The official MCP supports dollar amounts only for regular-hours market orders.
A dollar entry must be at least $1 with cent precision. Fractional quantities must
have at most six decimal places and also require regular-hours market orders.
A quantity entry replaces `dollar_amount` with `quantity = "0.02"` (fractional market)
or `quantity = "1"` (whole market). A whole-share limit entry uses `order_type = "limit"`
and an explicit `limit_price = "..."`; the fresh-quote and whole-cent price gates still
apply. Exactly one sizing field is accepted. An omitted `[entry]` retains the existing
budget-sized whole-share limit behavior. Fractional/dollar entries require
`exit.order_type = "market"`; whole-share exits may use market or limit.
No requested buy quantity is guessed from dollars for ownership or reconciliation.
The dollar amount and actual executed notional are separate journal/report fields.
Market quantity entries reserve the configured quote tolerance in their risk budget;
a market order cannot guarantee its fill price. Dollar orders are bounded by the
submitted dollar instruction. Any actual entry above `max_notional` is reported as
an incident after attempting the single exit, never as successful completion.

Capital, holding duration, poll count and session buffer are owner settings with
positive/finite validation, rather than hardcoded $1,000, six-hour or ETF restrictions.
The current position/risk settings may tighten the listed production ceilings.
Unknown fields, disabled LIVE, invalid combinations or nonprivate files halt.

The official broker must expose exactly one active agent-accessible account.
`live-check` reports its complete `account_sha256` digest without its account number.
Optionally add that independently verified digest to `[broker]` as
`account_sha256 = "..."` **before the first LIVE launch** to pin the account.
The first run also persists the authenticated account binding automatically;
a different account on restart halts. Account eligibility, cash-only capital
policy and broker permission checks remain mandatory.

## Check and launch

```bash
tradeagent live-check --config tradeagent.toml
# Optional private evidence file:
tradeagent live-check --config tradeagent.toml --output live-check.json

# Owner explicitly initiates real broker execution:
tradeagent run-once --live --config tradeagent.toml
```

`live-check` uses a transport incapable of review/place/cancel. It checks installed
contracts, fresh account/market state, configured account, requested entry feasibility,
broker trade-approval settings and existing run binding. READ_ONLY_READY means the
observed read-only prerequisites passed, not that an order has been submitted or
will fill. LIVE BLOCKED includes actual blockers, including closed/late session,
insufficient entry budget, unknown orders, authentication or schema errors.
An enabled application configuration does not change broker approval settings.
Broker-required customer approvals and exceptional review/placement approvals halt;
the owner must resolve those with Robinhood. No local flag impersonates approval.

The LIVE command performs fresh checks and runs the existing direct MCP transport,
`StandingLifecycle`, deterministic risk engine, durable journal and `OneShotRun`.
The local `OwnerPolicy` replaces cryptographic enrollment at the same guard and
wire boundaries. Review, exact request/state binding, review expiry, approval
consumption and submission markers still commit before network placement.
Separate human approvals retain their 30-second expiry. The immediate owner-operated
path bounds broker review age by the existing `risk.max_data_age_seconds` (120 seconds),
measured from request start and the oldest venue quote timestamp. Receiving a slow
response never resets its age. Review quotes, current market/risk state, exact payload,
account and permissions are checked again at the final HTTP boundary. Expired reviews
halt before placement; the runner does not automatically obtain another review.
No broker write is blindly retried. Classified transient idempotent reads retain
at most three attempts; authentication errors and malformed evidence do not retry.

## Recovery and automatic exit

One configuration and state directory own one lifecycle. The application creates
`tradeagent.toml.run.json` beside the configuration and `live-run.json` in the
state directory. These are durable recovery receipts, not signatures or separate
owner setup. Preserve the config, receipts and journal together. Repeating the
same LIVE command resumes/reconciles that run and never creates a second entry.
For a failed review, inspect the prior run with a transport incapable of broker writes:

```bash
tradeagent reconcile-once --config tradeagent.toml
```

This opens SQLite read-only and writes a separate timestamped reconciliation report.
It preserves the original report, journal, markers and receipt. It can read across a
wheel update while retaining the original config, account, risk and contract bindings;
LIVE execution still refuses a changed package. `HALTED` plus `NOT_SUBMITTED` and
`RECONCILED` identifies a failed attempt with no broker order or bot exposure.
`SUBMISSION_UNKNOWN` retains the strict missing-order blocker after an uncertain send.
`BROKER_CONFIRMED` requires broker identity and final accounting still checks terminal
orders, fills, fees, cash and holdings. Attempt timestamps are separate from actual
network-send timestamps. Neither diagnosis nor repeating an abandoned run replays entry.

Missing markers/journal, changed config/package/contracts/account or unexplained
positions halt. Do not delete state to rearm. Once the old round trip is closed,
use the explicit read-only archive operation with the same configuration:

```bash
tradeagent new-run --config tradeagent.toml
tradeagent live-check --config tradeagent.toml
# A separate explicit owner launch is required for any subsequent round trip:
tradeagent run-once --live --config tradeagent.toml
```

`new-run` checks the previous durable completion, authenticated account, all order
identities, exact bot-owned residual, current holdings and cash before moving its
journal, report and receipt into a private sibling `.history` directory. It also permits an explicitly requested archive after fresh proof that an abandoned
entry is `NOT_SUBMITTED`, with unchanged cash/holdings and no broker orders. This permits
recovery after a wheel correction without deleting evidence or weakening the LIVE code
pin. It refuses submitted unfinished or ambiguous runs and uses a stable configuration
lock shared with LIVE.
It never places orders. A crash while archiving fails closed with preserved evidence.
Each explicitly launched lifecycle remains limited to one entry and one exit.

Pending entries receive bounded polls and at most one durably reserved cancellation
attempt. Lost acknowledgments are reconciled without resubmission. A partial entry
exits only the exact confirmed quantity, including fractional shares, after the
remainder is terminal. No exit quantity is rounded or derived from buy dollars. A rejected or
partially filled exit remains a visible incident; the runner never submits another
exit to hide unresolved exposure. Unknown broker orders always halt safely.

The exit deadline is the earlier of the configured hold time after the latest
confirmed fill or `session_buffer_seconds` before regular close. The example uses
30 seconds of holding and a 60-second closing buffer. New exposure requires an
additional cancellation/poll window of at least one minute. The hold duration is
an execution-test setting, not a permanent strategy rule.
The LIVE runner waits while renewing its lease and monitoring ownership/cash.
Create `KILL` in the configured state directory to stop new exposure and request
the single risk-reducing exit. Fresh data, broker permissions and risk checks still
apply to that exit. Host/broker/market availability and fills cannot be guaranteed.
Do not stop a process holding a position without arranging owner recovery.

Private `report.json`, journal and events record actual order IDs, observed fills,
fees, positions, cash, executed notional, weighted execution price, exact residual,
exit and P&L when fully closed. `COMPLETED` requires both broker orders to be filled
and fresh final reconciliation. A cancelled partial entry that was fully exited is
`CLOSED_PARTIAL`; a partial/rejected/unknown exit is `HALTED`, with residual and
incident evidence. Estimated prices, ACKs and terminal orders without fills are
never labeled successful real trading. LIVE stdout retains status,
reason and reconciliation flags. Local incident records require owner attention;
no remote notification channel or recurring schedule is installed. Real broker
commissioning is separate from controlled-broker tests.

Paper continues to use the same controller with explicitly synthetic data:

```bash
tradeagent run-once --paper --state-dir /absolute/fresh/synthetic-state
```

It is software validation, not an execution replacement or market-performance claim.
