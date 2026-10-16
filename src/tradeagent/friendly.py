"""Short human summaries for people at a terminal.

Commands keep printing their full JSON record on stdout (scripts and the Codex
skill read it). When stderr is an interactive terminal, these summaries are added
there, so a person can see the outcome without reading the JSON.
"""

import sys

HELP = """\
TradeAgent: an AI analyst picks the trade, a deterministic engine places it.

See what it is doing:
  dashboard             local web dashboard: schedule, AI decisions, results, trades

Try it (no accounts needed):
  demo                  synthetic strategy lab: forecasts, scoring, learning
  run-once --paper      full buy-and-sell round trip against a simulated broker
  inspect               show what a research state directory has learned

Connect Robinhood:
  doctor                checklist of what is set up and what to do next
  login                 sign in through your browser (password never seen)
  logout                forget the saved session
  live-check            read-only readiness check; never places orders

Daily research (no orders):
  opportunity scan      rank today's unusual movers
  opportunity decide    freeze a TradePlan or NO_TRADE for the shortlist
  opportunity show      latest plan and research status
  opportunity compare   how Quant, Codex and both together have done so far

Real money (only when you run it):
  run-once --live       one entry from your config, then the automatic exit
  status                read-only state of the current live run
  autotrade install     one small LIVE trade each trading day, on a timer
  recover               finish an interrupted run; only ever exits

Run `tradeagent <command> --help` for options. Docs: README.md and docs/.
Advanced research commands: init, scan, resolve, learn-daily, evolve-weekly,
retire, retire-lesson, import-execution, replay-history, new-run, reconcile-once.
"""


def show(text):
    if text and sys.stderr.isatty():
        print("\n" + text, file=sys.stderr)


def demo(result):
    return (
        f"Demo {result.get('status', '?')}: synthetic data only, "
        f"{result.get('real_broker_calls', 0)} broker calls.\n"
        f"  {result.get('prediction_count', 0)} forecasts recorded and scored, "
        f"{result.get('daily_learning_runs', 0)} learning runs, "
        f"{result.get('challengers_created', 0)} challenger strategies created.\n"
        "  These numbers exercise the machinery; they are not evidence of profit."
    )


def paper(result):
    lines = [f"Paper round trip: {result.get('status', '?')} (simulated broker, no real orders)"]
    for order in result.get("orders", []):
        lines.append(
            f"  {order.get('side', '?'):4} {order.get('cumulative_quantity', '?')} "
            f"{order.get('symbol', '?')} @ {order.get('average_price', '?')} "
            f"({order.get('state', '?')})"
        )
    if result.get("realized_pnl") is not None:
        pnl = str(result["realized_pnl"])
        sign, value = ("-", pnl[1:]) if pnl.startswith("-") else ("", pnl)
        lines.append(f"  Realized P&L: {sign}${value}")
    return "\n".join(lines)


def live_check(result):
    note = result.get("broker_compatibility_summary")
    text = _live_check(result)
    return text + ("\n  Note: " + note if note else "")


def _live_check(result):
    if result.get("live_ready"):
        entry = result.get("configured_entry") or {}
        symbol = entry.get("symbol") or "your first allowed symbol"
        if entry.get("dollar_amount"):
            amount = f"${entry['dollar_amount']} of {symbol}"
        elif entry.get("quantity"):
            amount = str(entry["quantity"]) + " shares"
        else:
            amount = "a budget-sized position"
        return (
            "READY: signed in, account and permissions verified. No order was placed.\n"
            f"  `tradeagent run-once --live` would buy {amount} (cap "
            f"${result.get('configured_max_notional', '?')}) and exit automatically."
        )
    reasons = list(result.get("blockers") or [])
    if result.get("connectivity_blocker"):
        reasons.insert(0, result["connectivity_blocker"])
    lines = ["NOT READY (nothing was placed):"]
    lines += [f"  - {reason}" for reason in reasons] or ["  - see the JSON above"]
    return "\n".join(lines)


def halted(reason):
    return f"Stopped: {reason}"
