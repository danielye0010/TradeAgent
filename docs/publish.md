# Public results page

`tradeagent publish` turns the daily research into your agent's public track record:
every day's frozen pick and its result after costs, next to SPY over the same hour and a
random pick from the same shortlist, the latest reasoning, and, with Claude installed,
which AI does better. It updates itself
after every close. Ours is at <https://danielye0010.github.io/TradeAgent/>.

The page is built from the paper research only. Your live account, balances, orders and
settings are never read or published.

## Set up

You need the daily research running (and, for the GPT vs Claude parts, the
[Claude comparison](claude.md)), plus a GitHub repository you can push to; your fork of
TradeAgent works.

1. Let git push from this computer, once:

   ```bash
   gh auth login        # or use an SSH remote such as git@github.com:you/TradeAgent.git
   gh auth setup-git
   ```

2. Publish once by hand and check the result:

   ```bash
   tradeagent publish run --remote https://github.com/you/TradeAgent.git
   ```

   This builds the page and pushes it to the repository's `gh-pages` branch.

3. Turn on GitHub Pages: in the repository, **Settings → Pages → Build and deployment →
   Deploy from a branch**, branch `gh-pages`, folder `/ (root)`. The page appears at
   `https://you.github.io/TradeAgent/` (or under your custom domain).

4. Publish automatically after every close:

   ```bash
   tradeagent publish install --remote https://github.com/you/TradeAgent.git --enable
   ```

   The timer runs at 17:20 New York time on weekdays, after both research services have
   scored the day. If the computer was off, it publishes when it next starts.

`install` and `run` find the installed research timers; pass `--research-dir` and
`--claude-dir` to point at other directories. To preview without pushing:
`tradeagent publish build --out site/` and open `site/index.html`.
To stop: `tradeagent publish uninstall` (the published page stays up).

## What the page shows

- A track-record panel: the agent's cumulative net return, its excess over SPY over the
  same hours and over a random pick from the same shortlist, days ahead of SPY, hit rate
  and trading days.
- Performance: a chart and a table (cumulative, average per day, hit rate, best and worst
  day) for the agent, Claude, the quant engine alone, the random pick and SPY.
- The pipeline from the universe to the exit, and how TradeAgent differs from asking an
  LLM directly.
- The latest decision with each model's thesis, catalyst and sources.
- GPT or Claude: both models, and their head-to-head once both have scored sessions. The
  verdict waits for 20 trading days, as on the dashboard.
- Every decision, newest first.

All times are New York time (ET). Returns are modeled from market prices after spread
and slippage, not live fills, and are summed rather than compounded; the footer says so.
