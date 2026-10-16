# Dashboard

`tradeagent dashboard` starts a local web page that shows what TradeAgent is doing:
the day's schedule, each frozen AI decision and why it was made, how each research
arm is scoring, and every live or paper trade.

```bash
tradeagent dashboard            # your own state: installed timers and the live run
tradeagent dashboard --demo     # a synthetic workspace, no accounts needed
```

It prints a local address (default `http://127.0.0.1:8642/`) and opens it in a
browser. On WSL, open the printed address in a Windows browser if nothing opens.
Stop it with Ctrl+C.

## What each page shows

| Page | Answers |
| --- | --- |
| **Overview** | Is anything running or due? Today's session as a timeline with the scheduled decide/score/report steps, the latest decisions, autopilot timers, live settings and the current trade, and the setup checklist. |
| **Activity** | Every session, trade and learning run in time order; filter to AI decisions, trades or problems. |
| **Research** | Per protocol: how many paired sessions exist, each arm's positions and average net return, its return over SPY with a 95% range, and its verdict against Random. A cumulative chart appears from 20 paired sessions. Also the strategy lab's current weights. |
| **Trades** | Live and paper runs with fills, P&L, reconciliation and the step-by-step execution timeline. |

Click any decision to see the frozen record: the shortlist, the scanner numbers,
Codex's thesis, catalyst, invalidation and sources (price-only and news-reading notes
side by side for `daily-news-v2`), what each arm held and how it did.

Every number carries its origin: **LIVE**, **PAPER**, **Shadow** (prospective research,
no orders) or **Synthetic**. Comparisons get a verdict only after 20 paired sessions,
and only when the 95% range excludes zero; before that the page says how many
sessions are still needed.

## Where the data comes from

The dashboard reads the files TradeAgent already writes:

- **Installed timers:** `~/.config/systemd/user/tradeagent-*.service` give each job's
  state directory and protocol; `systemctl --user show` gives next and last run.
- **Daily research:** `daily-deployment.json`, `daily-status.json` and the append-only
  records in `experience.sqlite3` in each state directory.
- **Live trading:** `~/.local/state/tradeagent/live` (current run) and
  `live.history/` (archived runs); the summary of `~/.config/tradeagent/tradeagent.toml`
  (symbols, amount, cap, hold time only).
- **Anything else:** add directories with `--state-dir DIR` (repeatable): a daily
  research directory, a live or paper run directory, or a strategy lab such as the
  output of `tradeagent demo`.

```bash
tradeagent dashboard --state-dir ~/tradeagent-paper --state-dir ~/tradeagent-demo
```

## Read-only by design

- Binds to `127.0.0.1` only and rejects requests whose `Host` header is not local,
  which blocks DNS-rebinding pages from reading it.
- Answers `GET` only; there is no endpoint that places orders, changes settings or
  starts jobs. SQLite files open with `mode=ro`.
- Never reads tokens or the OAuth session. From the TOML it shows only symbols,
  dollar amount, cap and hold time.
- Strict Content-Security-Policy, no external scripts, fonts or trackers; works
  offline.

## Demo workspace

`--demo` builds a workspace once (about a minute) in
`~/.cache/tradeagent/dashboard-demo`, then reuses it:

- the strategy lab demo (`tradeagent demo`);
- one paper round trip on the simulated broker;
- 21 sessions of the daily four-arm protocol, run through the real pipeline on
  generated market data with a stand-in for Codex.

Everything in it is tagged synthetic. `--rebuild-demo` regenerates it; `--demo-dir`
puts it elsewhere.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--port` | `8642` | First port to try; the next nine are tried if it is taken. |
| `--state-dir DIR` | none | Extra state directory to show (repeatable). |
| `--no-discover` | off | Skip installed timers and the default live directory. |
| `--demo`, `--demo-dir`, `--rebuild-demo` | off | Synthetic workspace (see above). |
| `--no-browser` | off | Print the address without opening a browser. |
