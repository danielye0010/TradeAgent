# One-shot owner execution

The installed CLI owns an isolated execution-canary lifecycle without an LLM.
Paper and owner-launched LIVE operation use `OneShotRun`, `StandingLifecycle`,
`OfficialExecutionAdapter` and the existing durable order engine. The controller
adds scheduling and ownership checks; the existing engine owns review, durable
reference identity, authorization consumption, submit, cancel and fill accounting.
No research strategy, prediction or evidence database supplies this canary.

## Independent Linux installation

Build the wheel and install it in a separate environment from SHADOW:

```bash
python3 -m venv .build-venv
.build-venv/bin/pip install build
.build-venv/bin/python -m build
python3 -m venv .execution-venv
.execution-venv/bin/pip install dist/tradeagent-0.2.0-py3-none-any.whl
.execution-venv/bin/tradeagent run-once --paper --state-dir work/fresh-one-shot
```

Keep SQLite and locks on persistent local Linux storage. The wheel contains its
broker contracts and needs neither a source checkout nor Codex to run. No service
or timer is installed by these commands. OpenSSL is required for owner signature
verification; the OAuth helper remains outside the repository and package.

## Broker reads and current contracts

```bash
tradeagent live-check --oauth-helper /absolute/external/oauth-helper \
  --root /absolute/source-checkout --output work/live-check/report.json
```

`live-check` cannot review, place, cancel or authorize orders, even when owner
credentials are available. It checks authenticated metadata against pinned read
contracts and separately compares write contracts. It reads current account state
and broker trade-approval settings without printing balances, holdings or account
identifiers. Its status remains `LIVE BLOCKED` until owner setup is supplied; a
successful check does not arm the application or prove a real order can fill.

The frozen MCP 1.7.0 contracts include the authenticated customer-approval fields
on review and placement responses, and `get_trade_approval_setting`. Whole-share
limit review/place inputs and cancel/order-query schemas were unchanged. Required
fields remain validated. Explicit or uncertain customer approval blocks execution;
a placement approval object is not treated as an order acknowledgment.

Quotes and the L2 book are separate timestamped snapshots. Their inside prices
can differ during market movement. Execution bid/ask, available size and time are
normalized from the same L2 book; quote bid/ask freshness is checked before that
normalization, and last-trade valuation keeps its own timestamp. Stale/future data,
crossed books, insufficient depth and spread limits continue to halt execution.
The SHADOW provider validates read contracts only; unrelated write schema drift
cannot prevent a read-only research service from starting.

Only classified transport failures on idempotent reads retry: at most three
attempts, with 0.2/0.4-second backoff. HTTP 429/502/503/504 and network errors are
eligible. Authentication failures, malformed data and contract drift do not retry.
No review, place or cancel is automatically replayed.

## Owner-operated standing authorization

These commands are for the owner to run from their own terminal. The application
never generates a production signing key, signs a production grant, changes broker
approval settings or resolves an exceptional broker approval automatically.

First obtain/configure the dedicated Robinhood account and external noninteractive
OAuth helper. See [Prospective operation](prospective-shadow.md) for the helper
contract and [Robinhood trade approvals](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
for broker settings. Some orders may still require exceptional approval.

Prepare an unsigned request using only authenticated reads:

```bash
umask 077
tradeagent prepare-once --oauth-helper /absolute/external/oauth-helper \
  --state-dir /absolute/linux/one-shot-state --max-notional 25 \
  --hold-seconds 3600 --polls 3 --output /absolute/private/request.json
```

Review the request and its limits. $25 is a default ceiling, not a promise that
QQQ or IWM can be purchased. The runner returns `NO_TRADE` if the configured
ceiling or available funds cannot buy a whole share. It never raises capital to
force a transaction. An owner may explicitly choose a different cap, bounded by
$1,000 and the unchanged risk engine. The request authorizes at most one entry,
its corresponding owned-position exit, and one cancellation attempt per known
pending order. It is valid for at most 24 hours and binds account, installed code,
configuration, risk, schemas, universe, exit/poll limits and absolute state path.

Using an owner-controlled Ed25519 key outside the repository, sign the exact
request bytes in a separate owner terminal or offline environment:

```bash
openssl pkeyutl -sign -inkey /absolute/private/owner-ed25519.pem -rawin \
  -in /absolute/private/request.json -out /absolute/private/request.sig
sha256sum /absolute/private/owner-public.pem
```

Keep the request, signature and public-key files owner-only (mode 0600). Check the
public-key fingerprint through the owner's trusted key setup, then install:

```bash
tradeagent setup-once --request /absolute/private/request.json \
  --signature /absolute/private/request.sig --public-key /absolute/private/owner-public.pem \
  --public-key-sha256 OWNER_VERIFIED_SHA256 \
  --authorization-dir /absolute/private/one-shot-authorization
```

Setup verifies the signature and fingerprint and creates a new mode-0700 trust
directory; it never replaces existing trust silently. It does not arm or launch a
trade. The legacy global release/signing-key gates remain closed for legacy tools;
this explicit owner-installed one-shot grant is a distinct supported setup path.

After the owner has reviewed configuration and broker eligibility, the implemented
owner launch interface is:

```bash
tradeagent run-once --live --authorization-dir /absolute/private/one-shot-authorization \
  --oauth-helper /absolute/external/oauth-helper
```

This command can submit real orders; it is not a connectivity test. It was not
executed against Robinhood during development. Missing/expired/changed authority,
unsupported account state, approval requirements, stale data or insufficient exit
capability halt execution. Authentication and passing mocked tests do not establish
LIVE commissioning or profitability.

## Ownership, recovery and shutdown

One fresh isolated state directory owns one run. The signed authorization is also
bound to that directory's marker/journal; missing state cannot rearm an already
bound grant. Never delete state or reuse an authorization to attempt another entry.

Only a position absent initially and attributed to this run's persisted broker
references can be sold. Pending entries are reconciled for the bounded poll count,
then one known-order cancellation is reserved durably before network I/O. A lost
cancel acknowledgment is reconciled, never automatically replayed. A partial entry
exits only confirmed whole shares after the remainder reaches a terminal state.
Unknown absent orders and unexplained account movement remain incidents.

The holding deadline is the earlier of the signed hold interval after confirmed
fill or ten minutes before the regular session close. New entries require at least
one additional minute before that deadline. LIVE waiting renews the lease and
monitors authoritative ownership/cash. Restart resumes an existing order/position
without another entry. The single exit is never repeated after a rejection or
partial fill; an unresolved residual is a visible incident requiring owner action.

Create `KILL` in the run directory to stop new exposure and request the single
risk-reducing exit. Authorization, market/session and broker checks still apply.
Do not stop the process while it holds a position without arranging recovery.
There is no guarantee of fills or exit while the broker/host/market is unavailable.

`report.json` and the existing engine journal record orders, fills, fees, cash,
positions, times, incidents and P&L only when the position fully closes. LIVE stdout
is a status summary; full private evidence remains owner-only on disk. High-priority
incident events are local; no external notification channel has been configured.

Paper uses the same controller and authorization specialization with an explicitly
simulated owner grant. Its $10 IWM/$12 QQQ prices and virtual 2026-10-05 clock are
invented software fixtures, excluded from strategy performance. Reuse its state
path to test recovery without another entry; use a fresh directory for another
entirely synthetic run. Exit status 0 means `COMPLETED` or `NO_TRADE`; 2 means
blocked/halted. Fault injection may exit 1 to model a process crash.

Long-term OAuth renewal/revocation, Windows/WSL uptime, broker exceptions and
remote incident delivery require separate operational commissioning. No recurring
LIVE timer or service is installed or enabled by this feature.