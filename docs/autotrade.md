# Autotrade

`tradeagent autotrade` places one small LIVE trade per trading day on a timer, with
nothing to run by hand once it is installed.

```bash
tradeagent autotrade install --enable          # weekdays 10:15 New York time
tradeagent autotrade install --enable --at 11:00
systemctl --user list-timers tradeagent-autotrade.timer
tradeagent autotrade uninstall                  # stop and remove the timer
```

## What a run does

1. Skips the day if the market is closed or `KILL` exists in the live state directory.
2. Finishes any interrupted earlier trade (exit only). If that cannot be closed, no new
   trade is opened.
3. Scans the market, then freezes an experimental decision for the top three movers.
4. Buys only when a predefined quant long signal is eligible today; otherwise records
   `NO_TRADE` and stops.
5. Hands the plan to the LIVE engine: the `[entry] dollar_amount` from your TOML, every
   risk limit, the timed exit after `hold_seconds`, and reconciliation.

At most one new entry per New York trading day. A missed morning (computer asleep,
WSL closed) is skipped, never traded late.

## Settings

Everything comes from `~/.config/tradeagent/tradeagent.toml`:

| Setting | Meaning |
| --- | --- |
| `[entry] dollar_amount` | Amount per trade, `$1` to `$5`. |
| `[live] symbols` | Only these symbols can be bought. |
| `[exit] hold_seconds` | Sell automatically after this long. |
| `[risk] max_new_exposure_fraction` | One trade may use at most this share of the account. |

`install` prints `account_value_needed`: the smallest account value at which the risk
fractions still admit your amount. With `$1` and the default `0.10`, that is `$10`;
any loss below `$10` would block the next entry. For a small account, raise
`max_new_exposure_fraction` (for example `"0.15"`) to keep room.

## Requirements

- systemd user services (on WSL: `systemd=true` in `/etc/wsl.conf`), and the computer
  and WSL running at the scheduled time.
- A working Robinhood sign-in (`tradeagent login`, `tradeagent live-check`).
- Codex is not needed: the experimental policy uses the quant signal only.

Each run prints a JSON log to the journal:

```bash
journalctl --user -u tradeagent-autotrade.service -n 50
```

Trades appear on the dashboard's **Trades** page.
