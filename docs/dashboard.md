# Dashboard

`tradeagent dashboard` starts a local web page that shows what TradeAgent is doing:
your live account and today's trade first, then each frozen AI decision and why it
was made, how each research group is scoring, and every trade.

```bash
tradeagent dashboard
```

It prints a local address (default `http://127.0.0.1:8642/`) and opens it in a
browser. On WSL, open the printed address in a Windows browser if nothing opens.
Stop it with Ctrl+C.

## What each page shows

| Page | Answers |
| --- | --- |
| **Account** | How is my money doing? The live account value, profit in dollars and percent, a value chart (hover to see any point; 1W / 1M / 3M / All), recent trades, and a card for today: waiting for Codex, what it picked, holding with the sell time, the result, or why nothing was bought. Also the autotrade settings and Codex's paper record. |
| **Decisions** | Today's session as a timeline with the scheduled steps, the latest frozen decisions, timers, live settings and the setup checklist. **Full activity** lists every session, trade and learning run; filter to AI decisions, trades or problems. |
| **GPT vs Claude** | Is Claude picking better than Codex on the same shortlist? Each model's added-up net return, sessions where each did better, how often they picked the same stock, a verdict on the difference after 20 sessions, a chart, and every session with both picks; select one to read both models' reasoning side by side. Switch between the news and price-only questions. See [Claude next to Codex](claude.md). |
| **Research** | Per protocol: how many paired sessions exist, each group's positions and average net return, its return over SPY with a 95% range, its verdict against Random, and a cumulative net-return chart from the second scored session. Also the strategy lab's current weights. |
| **Trades** | Total profit, closed trades and win rate, then every trade newest first; select one for its prices, times and execution timeline. |

Gains and losses use green up / red down by default and red up / green down in Chinese;
the button in the header switches between them.

Click any decision to see the frozen record: the shortlist, the scanner numbers,
Codex's thesis, catalyst, invalidation and sources (price-only and news-reading notes
side by side for `daily-news-v2`), what each arm held and how it did.

Every number carries its origin: **LIVE** for real orders, **PAPER** for the
autopilot's daily research and simulated runs. Comparisons get a verdict after 20
paired sessions, once the 95% range excludes zero; until then the page shows how many
sessions are in.

## Where the data comes from

The dashboard reads the files TradeAgent already writes:

- **Installed timers:** `~/.config/systemd/user/tradeagent-*.service` give each job's
  state directory and protocol; `systemctl --user show` gives next and last run.
- **Daily research:** `daily-deployment.json`, `daily-status.json` and the append-only
  records in `experience.sqlite3` in each state directory.
- **Live trading:** `~/.local/state/tradeagent/live` (current run) and
  `live.history/` (archived runs), including the account value and cash each run started
  from; the summary of `~/.config/tradeagent/tradeagent.toml` (symbols, amount, cap, hold
  time only).
- **Autotrade:** the daily outcome files in the autotrade timer's state directory.
- **Anything else:** add directories with `--state-dir DIR` (repeatable): a daily
  research directory, a live or paper run directory, or a strategy lab.

```bash
tradeagent dashboard --state-dir ~/tradeagent-research/news
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

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--port` | `8642` | First port to try; the next nine are tried if it is taken. |
| `--state-dir DIR` | none | Extra state directory to show (repeatable). |
| `--no-discover` | off | Skip installed timers and the default live directory. |
| `--no-browser` | off | Print the address without opening a browser. |

## Real saved results

The [2026-10-16 evidence snapshot](evidence/2026-10-16/README.md) provides the
real-market Paper records directly, with winning, losing, cash and pending results.
Use it to inspect numbers without relying on a screenshot. It contains no broker
fills and does not demonstrate live-account profitability.
