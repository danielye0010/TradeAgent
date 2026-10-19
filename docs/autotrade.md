# Autotrade

`tradeagent autotrade` places a small LIVE copy of the daily news-reading AI pick, with
nothing to run by hand once it is installed. By default it follows Codex: the
`codex_news` arm of the `daily-news-v2` research service. With `--model claude` it
follows Claude's answer to the same question instead (the `claude_news` arm of
[`daily-news-claude-v1`](claude.md)). Either way it copies the same frozen selection, the
same entry minute and the same one-hour hold, for your configured dollar amount.

```bash
tradeagent autotrade install --enable                       # finds the installed v2 timer
tradeagent autotrade install --enable --follow-dir DIR      # or name its state directory
tradeagent autotrade install --enable --model claude        # follow Claude instead of Codex
systemctl --user list-timers tradeagent-autotrade.timer
tradeagent autotrade uninstall                              # stop and remove the timer
```

The `daily-news-v2` research timer must already be installed and running (and, for
`--model claude`, the Claude comparison timer too); autotrade only reads frozen
decisions and never re-decides.

## What a run does

The timer starts at 10:30 New York time, when the research decision window opens.

1. Skips the day if the market is closed or `KILL` exists in the live state directory.
2. Waits up to nine minutes for that day's frozen decision of the followed model.
3. Reads its selection (`codex_news` or `claude_news`). Cash, an unavailable assessment, an invalid window
   or a symbol outside your `[live] symbols` is recorded as `NO_TRADE`.
4. Finishes any interrupted earlier trade (exit only) and archives the closed one. If
   that cannot be closed, no new trade is opened.
5. Waits for the decision's entry minute, then hands the order to the LIVE engine: your
   `[entry] dollar_amount`, every risk limit, the sell one hour after the fill, and
   reconciliation. If the entry window has already passed, the day is `EXPIRED`.

At most one new entry per New York trading day. A missed morning (computer asleep,
WSL closed) is skipped, never traded late. Each day's outcome is written to
`~/.local/state/tradeagent/autotrade/YYYY-MM-DD.json` with the research decision id.

## Settings

Everything comes from `~/.config/tradeagent/tradeagent.toml`:

| Setting | Meaning |
| --- | --- |
| `[live] enabled` | Must be `true`; this is the LIVE master switch. |
| `[entry] dollar_amount` | Amount per trade, `$1` to `$5`. |
| `[live] symbols` | Only these symbols can be bought. Include the research universe to copy every pick; `install` lists the research symbols you have left out. |
| `[exit] hold_seconds` | Must be `3600`, the research horizon. |
| `[risk] max_new_exposure_fraction` | One trade may use at most this share of the account. |

`install` prints `account_value_needed`: the smallest account value at which the risk
fractions still admit your amount. With `$1` and the default `0.10`, that is `$10`;
any loss below `$10` would block the next entry. For a small account, raise
`max_new_exposure_fraction` (for example `"0.15"`) to keep room.

## Requirements

- The `daily-news-v2` research timer (and its Codex login) running on the same machine.
- systemd user services (on WSL: `systemd=true` in `/etc/wsl.conf`), and the computer
  and WSL running from 10:30 to about 11:45 New York time.
- A working Robinhood sign-in (`tradeagent login`, `tradeagent live-check`).

Each run prints a JSON log to the journal:

```bash
journalctl --user -u tradeagent-autotrade.service -n 50
```

Trades appear on the dashboard's **Trades** page.
