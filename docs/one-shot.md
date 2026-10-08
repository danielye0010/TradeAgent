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
pip install /absolute/path/tradeagent-0.2.1-py3-none-any.whl
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

[exit]
hold_seconds = 3600
polls = 3
```

The initial universe may be a nonempty subset of the ordinary QQQ/IWM ETFs.
One-shot capital defaults to $25, with an absolute software ceiling of $1,000;
changing it is an explicit owner configuration choice. There is no leverage,
shorting, options, fractional-share fallback or automatic capital increase.
The existing risk engine can impose a smaller budget. If no whole share fits,
the command returns NO_TRADE. At current prices the $25 default may prevent any entry.
Position/risk settings may tighten the listed production ceilings but cannot weaken
them. Hold time is 0–21,600 seconds and polls are 1–30; unknown fields and malformed,
nonprivate or disabled configurations halt before execution.

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
contracts, fresh account/market state, configured account, whole-share feasibility,
broker trade-approval settings and existing run binding. READ_ONLY_READY means the
observed read-only prerequisites passed, not that an order has been submitted or
will fill. LIVE BLOCKED includes actual blockers, including closed/late session,
insufficient whole-share capital, unknown orders, authentication or schema errors.
An enabled application configuration does not change broker approval settings.
Broker-required customer approvals and exceptional review/placement approvals halt;
the owner must resolve those with Robinhood. No local flag impersonates approval.

The LIVE command performs fresh checks and runs the existing direct MCP transport,
`StandingLifecycle`, deterministic risk engine, durable journal and `OneShotRun`.
The local `OwnerPolicy` replaces cryptographic enrollment at the same guard and
wire boundaries. Review, exact request/state binding, review expiry, approval
consumption and submission markers still commit before network placement.
No broker write is blindly retried. Classified transient idempotent reads retain
at most three attempts; authentication errors and malformed evidence do not retry.

## Recovery and automatic exit

One configuration and state directory own one lifecycle. The application creates
`tradeagent.toml.run.json` beside the configuration and `live-run.json` in the
state directory. These are durable recovery receipts, not signatures or separate
owner setup. Preserve the config, receipts and journal together. Repeating the
same LIVE command resumes/reconciles that run and never creates a second entry.
Missing markers/journal, changed config/package/contracts/account or unexplained
positions halt. Do not delete state to rearm; a new one-shot requires an explicitly
new config file and fresh isolated state path, after resolving the previous run.

Pending entries receive bounded polls and at most one durably reserved cancellation
attempt. Lost acknowledgments are reconciled without resubmission. A partial entry
exits only confirmed whole shares after the remainder is terminal. A rejected or
partially filled exit remains a visible incident; the runner never submits another
exit to hide unresolved exposure. Unknown broker orders always halt safely.

The exit deadline is the earlier of hold time after confirmed fill or ten minutes
before regular-session close. New exposure needs another minute before that deadline.
The LIVE runner waits while renewing its lease and monitoring ownership/cash.
Create `KILL` in the configured state directory to stop new exposure and request
the single risk-reducing exit. Fresh data, broker permissions and risk checks still
apply to that exit. Host/broker/market availability and fills cannot be guaranteed.
Do not stop a process holding a position without arranging owner recovery.

Private `report.json`, journal and events record actual order IDs, observed fills,
fees, positions, cash, exit and P&L when fully closed. LIVE stdout retains status,
reason and reconciliation flags. Local incident records require owner attention;
no remote notification channel or recurring schedule is installed. Real broker
commissioning is separate from controlled-broker tests.

Paper continues to use the same controller with explicitly synthetic data:

```bash
tradeagent run-once --paper --state-dir /absolute/fresh/synthetic-state
```

It is software validation, not an execution replacement or market-performance claim.
