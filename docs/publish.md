# Public results page

`tradeagent publish` turns the daily research into a public web page: every day's frozen
picks from GPT (Codex) and Claude, their results after costs, SPY over the same hour, a
running chart, the latest reasoning and a head-to-head scoreboard. It updates itself
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

- Each model's added-up return, SPY over the same hours, and the number of trading days.
- A chart of GPT, Claude, SPY and a random pick, day by day.
- The latest picks with each model's own reasoning and news sources.
- Head to head: on how many days each model did better, and how often they agreed. The
  verdict waits for 20 trading days, as on the dashboard.
- Every pick, newest first.
- How it works, and how to run it yourself.

Returns are modeled from market prices after spread and slippage, not live fills. The
page says so in its footer.
