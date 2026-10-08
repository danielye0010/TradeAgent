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
pip install /absolute/path/tradeagent-0.3.0-py3-none-any.whl
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

One configuration and local Linux state directory own one lifecycle. Preserve its
`live-run.json`, SQLite journal and reports. New lifecycles use a single marker;
legacy external receipts remain readable and are preserved. Owners do not manage
hashes or signing material. Repeating a launch never creates another entry.

```bash
tradeagent status --config tradeagent.toml
tradeagent recover --config tradeagent.toml
```

`status` reads broker orders, holdings, exposure and reconciliation without modifying
execution evidence or invoking review/place/cancel. `reconcile-once` performs the same
checks and saves a separate timestamped report. `recover` manages only the existing
lifecycle. It reconciles uncertain submissions by durable client reference and broker
ID before any write, polls existing orders and resumes exit management. It never
creates a new entry or blindly retries placement.

`HALTED`, `NOT_SUBMITTED` and `RECONCILED` identify a resolved pre-submission failure.
`SUBMISSION_UNKNOWN` remains blocked until broker identity and accounting are proven.
Confirmed partial entry fills exit only their actual quantity after the entry is
terminal. A terminal rejected or partial exit remains visible; a separate owner
`recover` may submit a new exit after reconciling every previous order, fills, fees,
cash and exact remaining bot-owned sellable quantity. Unrelated holdings cannot be
sold. Ambiguous or open prior orders block another exit. Optional `max_exit_attempts`
can limit recovery; the default has no arbitrary permanent one-exit ceiling.

After verified closure or a no-submission failure:

```bash
tradeagent new-run --config tradeagent.toml
tradeagent live-check --config tradeagent.toml
# Explicit owner launch, after readiness succeeds:
tradeagent run-once --live --config tradeagent.toml
```

`new-run` verifies the recorded account, terminal order identities, fills, fees, cash
and zero bot-owned residual before archiving all journal/report/legacy receipt evidence
in a private sibling `.history` directory. It never places orders or deletes evidence.
Missing state, unresolved identities or unexplained movements require recovery, never
a reset. A finished lifecycle suppresses repeated launches until `new-run`.

Application/build identity is historical metadata. Compatible application upgrades do
not invalidate execution. State schema, execution protocol and broker contracts are
checked independently; incompatible state produces an actionable error and is retained.
The first schema migration records version 1 without removing any existing journal data.
Semantic TOML comparison accepts comments and formatting. Active lifecycle risk,
entry/exit parameters, account and ownership remain frozen; edited economic parameters
apply after `new-run`. Meaningful edits during a placement operation halt before send.

Use `broker.account_number` to select one accessible eligible account. Unrelated
equities and reliably valued assets contribute to account NAV and concentration;
unknown valuation, permissions, leverage or accounting still block execution. Execution
remains US long equity/ETF only. The default entry symbol is the first configured
symbol. `entry.symbol` fixes it; an explicit ordered `entry.preferred_symbols` permits
fallback and records each rejection. There is no cheapest-symbol selection.

Owner settings support positive entry/max-order amounts, position count, concentration,
new and total exposure, cash reserve (including zero), daily loss/turnover, liquidity,
spread, hold duration and polling/recovery behavior without development ceilings.
Broker eligibility and available unleveraged funds still bound every entry. An exact
owned-position exit may bypass loss/turnover entry gates with
`exit.risk_reduction_on_limits = true`; data freshness, ownership, permissions,
sellability and accounting checks always apply.

The library's `validated_plan_entry` accepts an explicit matching, fresh long underlying
TradePlan/Prediction plus owner-configured sizing and preserves strategy/version,
snapshot and decision provenance. It shares execution risk checks. Research and SHADOW
do not invoke this boundary automatically.

The exit deadline is the earlier of the configured hold time after the latest
confirmed fill or `session_buffer_seconds` before regular close. The example uses
30 seconds of holding and a 60-second closing buffer. New exposure requires an
additional cancellation/poll window of at least one minute. The hold duration is
an execution-test setting, not a permanent strategy rule.
The LIVE runner waits while holding its process lock and monitoring ownership/cash.
Create `KILL` in the configured state directory to stop new exposure and request
a risk-reducing exit. Fresh data, broker permissions and risk checks still
apply to that exit. Host/broker/market availability and fills cannot be guaranteed.
Do not stop a process holding a position without arranging owner recovery.

Private `report.json`, journal and events record actual order IDs, observed fills,
fees, positions, cash, executed notional, weighted execution price, exact residual,
exit and P&L when fully closed. `COMPLETED` requires both broker orders to be filled
and fresh final reconciliation. A cancelled partial entry that was fully exited is
`CLOSED_PARTIAL`; a partial/rejected/unknown exit is `HALTED`, with residual and
incident evidence. Estimated prices, ACKs and terminal orders without fills are
never labeled successful real trading. LIVE stderr emits timestamped observed execution transitions. Stdout returns the full
JSON report with order IDs, quantities, costs, fees, P&L and reconciliation flags. Local incident records require owner attention;
no remote notification channel or recurring schedule is installed. Real broker
commissioning is separate from controlled-broker tests.

Paper continues to use the same controller with explicitly synthetic data:

```bash
tradeagent run-once --paper --state-dir /absolute/fresh/synthetic-state
```

It is software validation, not an execution replacement or market-performance claim.
