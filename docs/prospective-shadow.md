# Prospective shadow deployment

This service collects forward-only QQQ/IWM/SPY SIP market snapshots. It has no
broker/account/order client or execution scheduler. The research state is an
independent cold database in `work/prospective/experience.sqlite3`; the separate
`collection.sqlite3` contains normalized source facts, actual local receipt times,
first-observed immutable minute bars, source failures and scheduler receipts.
Raw revisions are retained in receipts without rewriting first-observed bars.
No historical source or commissioning database is opened by this service.

The frozen configuration is QQQ and IWM against SPY, one-minute completed bars,
09:32 America/New_York, 60-minute outcomes, prospective evidence and no options.
October 7, 2026 is deployment/forward health collection only. The first eligible
target is October 8, 2026 at 09:32 EDT (13:32 UTC). XNYS sessions and New York
timezone rules select subsequent targets, including holidays and DST.

## Arming blocker

**NOT ARMED** with the current frozen minute-bar contract. A decision snapshot
requires its final completed minute to end at 09:32 and actually be available by
09:32. Alpaca publishes minute bars after the minute boundary. Receipt times
must remain actual receipt times, so the final bar fails that cutoff. See
[Alpaca's minute-bar timing](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).
The collector and scheduler may run, but deployment must not be described as an
armed prediction experiment. SIP entitlement/connectivity failures are reported
separately in status. There is no automatic IEX/delayed-feed substitution.

The scheduler never runs a missed decision late or labels a late prediction
contemporaneous. Artificial exact-boundary fixtures verify integration with
`scan`, `resolve`, `learn_daily` and `evolve_weekly`; they do not establish that
Alpaca can meet the source cutoff. Resolution waits for an exact contiguous
60-minute source path and actual receipt availability. Learning and existing
weekly evolution use only prospective evidence and the existing ISO-week
identity. Strategy parameters, learner formulas and promotion rules are unchanged.

## Local installation

Run the operator setup in a real interactive WSL terminal:

```bash
.venv/bin/python -m tradeagent.prospective.access
.venv/bin/python scripts/install_shadow.py
```

Both inputs use `getpass`; inability to disable echo aborts setup. They are piped
to `systemd-creds encrypt --user --name=alpaca` using stdin. Only a user-scoped
encrypted credential is stored in the private user-local application-data
directory outside the repository (directory 0700, file 0600). No plaintext
persistent copy, JSON, credentials in environment/argv, or credential fingerprints
are created. systemd decrypts a private runtime credential through
`LoadCredentialEncrypted`; unattended runs never prompt. Runtime input is
owner/mode checked. Credentials are market-data-only inputs to a fixed HTTPS
data endpoint; redirects are rejected. Vendor errors/bodies and tracebacks are
excluded from logs. Core dumps are disabled for the service.

`scripts/install_shadow.py` installs and enables the current user's systemd
unit, `tradeagent-prospective.service`, with restart-on-failure, a private umask
and no-new-privileges. Enable user lingering with an administrative
`loginctl enable-linger` operation. Run `scripts/install_shadow_host.ps1` on
Windows to install the current-user Task Scheduler WSL lifetime anchor.
That task keeps Ubuntu open, retries every five minutes and starts at user
logon. It stores no Windows password and its only Linux workload is `sleep`.
[WSL systemd services alone do not keep the WSL instance alive](https://learn.microsoft.com/en-us/windows/wsl/systemd).
Machine power-off/sleep or lack of Windows user logon can still cause a missed
decision; after resumption the service records it and does not backfill.

Inspect health without any credential command:

```bash
systemctl --user status tradeagent-prospective.service
cat work/prospective/STATUS.md
```

`status.json` contains heartbeat/PID, next eligible target, latest collection,
prediction/resolution/learner timestamps, unresolved counts, learned weights,
challenger/promotion counts, integrity checks and explicit zero broker counts.
The in-process audit compares credential values against tracked repository
files, this deployment's generated artifacts, its unit, its process arguments
and its service journal. Only pass/fail flags are saved. The service halts if
this isolation check fails. No credential values or fingerprints appear in the
handoff. Runtime databases, receipts and private reports remain Git-ignored.

Stop with `systemctl --user disable --now tradeagent-prospective.service` and
disable the named Windows task. Preserve the encrypted credential and research
state for operator-controlled recovery; the installer never overwrites a key.
