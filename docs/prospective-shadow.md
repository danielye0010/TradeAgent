# Prospective shadow operation

Default: **Robinhood market data + Robinhood execution**. Optional:
**Alpaca market data + Robinhood execution**. This service performs only the
market-data and SHADOW prediction/outcome path. It never reviews, places or cancels
orders and has no research-to-live bridge.

## Timing

A decision may consume only data available by its decision time. The frozen target
is **09:33 America/New_York** on eligible XNYS sessions from October 8, 2026.
Provider bar timestamps denote UTC minute starts. The normalizer sets end = start
+ 60 seconds and availability = actual local receipt. Forming bars, synthesized
gap-fill bars, stale quotes and post-cutoff receipts cannot enter a decision.

Snapshots require at least two contiguous opening minutes and matching symbol/SPY
endpoints, with final-bar age at most 120 seconds. A 30-second capture window permits
persistence after target while retaining the fixed information cutoff. Missed or
unavailable decisions are skipped, never repaired with later historical data.

The 3600-second horizon begins at decision time. Resolution requires every minute
for both symbol and benchmark through the exact endpoint. When the signal bar ends
before decision, entry is the decision-minute open observed later; predecision
movement is excluded. Missing minutes remain unresolved. First-observed facts
remain frozen; repeated requests and restarts cannot replace them.

XNYS holidays, weekends, early closes and New York DST use the shared calendar.
Startup after the opening minutes cannot backfill an opening prediction.
Provider semantics and capability limits are described in [Market data](market-data.md).

## Robinhood setup

Provide a local external OAuth helper for
https://agent.robinhood.com/mcp/trading. This project does not perform login,
store passwords/tokens, or implement OAuth refresh. The helper owns authentication
and must reside outside the repository, on local Linux storage, owned by the user,
executable, with no group/other permissions. Its stdout contract is:

~~~json
{"access_token": "<external token>", "expires_at": 1234567890,
 "resource": "https://agent.robinhood.com/mcp/trading"}
~~~

Use an actual future expiry, 30 seconds to 24 hours from the request.
Helper stderr and credential values are never logged. Interactive authentication
must be completed by the operator outside the unattended worker.

~~~bash
.venv/bin/python scripts/install_shadow.py --oauth-helper /absolute/external/helper
systemctl --user status tradeagent-prospective.service
cat work/prospective-robinhood/STATUS.md
~~~

The default helper path is $HOME/.local/libexec/robinhood-mcp-oauth-helper.
The service has no Alpaca credential directive. Missing Robinhood authentication or
a changed official schema fails closed with a sanitized blocker.
No silent fallback, login prompt, redirect or order operation is introduced.

## Optional Alpaca setup

~~~bash
.venv/bin/python -m tradeagent.prospective.access
.venv/bin/python scripts/install_shadow.py --market-data-provider alpaca
cat work/prospective-alpaca/STATUS.md
~~~

Alpaca uses hidden terminal input and systemd-encrypted credentials outside the
repository. Only explicit Alpaca mode adds LoadCredentialEncrypted=alpaca:...
Its SIP entitlement and publication latency must be verified independently.
No Alpaca credentials are read in Robinhood mode.

Both installers reuse tradeagent-prospective.service. Their default cold state
directories are provider-specific. An explicit --state-dir can select compatible
state; deployment markers refuse configuration/source changes. Previously collected
Alpaca state remains preserved separately, with no evidence conversion.

## Ownership and persistence

The systemd user service is the sole unattended owner. Its lifetime
experience.lock is acquired before deployment/database writes and excludes
overlapping service or one-shot research commands. Two-symbol prediction commits
are atomic; durable identities suppress duplicates across crashes and restarts.

The Windows TradeAgentProspectiveWSL task only keeps WSL available. IgnoreNew and
a Linux flock suppress duplicate lifetime helpers. It never starts a trading loop.
[Microsoft documents WSL lifetime behavior](https://learn.microsoft.com/en-us/windows/wsl/systemd).
User lingering and the existing Windows task remain separate host setup.

experience.sqlite3 owns predictions/outcomes; collection.sqlite3 owns normalized
market facts and scheduler steps. Only due unresolved forecasts trigger resolution.
Daily learning runs after outcomes commit and fails softly. Challenger proposals,
evaluation and promotion remain explicit research commands; the collector never
changes champion pointers. Unknown evidence sources and database uncertainty halt.

SHADOW mode is separate from health and blockers. Status reports run every 30
seconds, including receipt/prediction/resolution times and zero broker operations.
Status-file failure does not undo predictions or stop collection.

## Safe verification

~~~bash
tradeagent demo --demo-dir work/fresh-shadow-smoke
tradeagent execution simulate --demo-dir work/fresh-execution-smoke
systemctl --user status tradeagent-prospective.service
~~~

Use fresh synthetic directories. Outside regular hours, historical data reads can
verify provider capability but cannot establish a live-session prospective decision.
Observe actual opening receipts, aligned inputs and the exact-horizon resolution
before interpreting performance. Tests do not establish real fill behavior, alpha
or physical sleep/power recovery.

Stop with systemctl --user disable --now tradeagent-prospective.service and disable
the named Windows lifetime task. Preserve state and external authentication.
