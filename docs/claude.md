# Claude next to Codex (GPT vs Claude)

TradeAgent can ask Claude the same daily question it asks Codex, and score both on the
same trades. You can watch the two side by side on the dashboard's **GPT vs Claude**
page, and you can choose to trade Claude's pick instead of Codex's.

Claude runs through the Claude Code CLI with your Claude subscription (Pro, Max, Team
or Enterprise), the same way Codex runs through the Codex CLI with your ChatGPT sign-in.
No API key is used.

## How the comparison works

The `daily-news-v2` research service freezes a shortlist of three candidates at 10:30
New York time and asks Codex about them twice: once with price data only and once with
web search for news. The Claude comparison (`daily-news-claude-v1`) follows that service:

- It takes the shortlist v2 froze, with the same market evidence.
- It asks Claude the same prompt, with the same output schema and the same 5-minute
  deadline. The news arm may use web search; the price arm has no tools.
- Claude's picks are chosen with the same rules as Codex's and scored over the v2
  decision's own entry minute, one-hour exit and modeled costs.

| Claude arm | Paired Codex arm | Question |
| --- | --- | --- |
| `claude_news` | `codex_news` | Which stock, after reading the news? |
| `claude_price` | `codex_price` | Which stock, from price data alone? |
| `quant_claude_news` | `quant_codex_news` | The news pick, only when a quant signal agrees |

Fairness rules, frozen in the deployment:

- Claude must start within 60 seconds of the v2 shortlist being frozen.
- An answer frozen after the v2 entry time could have seen later news, so it is not
  scored and the arm is recorded as unavailable for that session.
- A failed or missing answer is unavailable, never counted as cash. Nothing is rerun or
  backfilled. A Claude-minus-Codex difference uses only the sessions where both answered.

The v2 research, its records and anything that follows them are only read, never
changed. The Codex arms keep running exactly as before.

## Set up

Install Claude Code inside WSL or Linux, as the same user that runs TradeAgent, and sign
in once with your Claude subscription:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude              # follow the browser sign-in; on WSL you may need to paste a code
```

Then check and start the comparison from the TradeAgent checkout with `.venv` active:

```bash
tradeagent claude doctor            # CLI found, subscription login, v2 research found
tradeagent claude doctor --probe    # also ask Claude once about the latest v2 shortlist
tradeagent claude install --enable
systemctl --user list-timers tradeagent-claude-compare.timer
```

`install` finds the installed `daily-news-v2` timer (or takes `--follow-dir DIR`) and
puts its records next to it in `daily-news-claude-v1/` (or `--state-dir DIR`). The timer
runs at 10:30 (decide), 12:10 (score) and 17:10 (report) New York time. To stop it:
`tradeagent claude uninstall`; the records are kept.

If `ANTHROPIC_API_KEY` is set, it is removed from the Claude process. A run that is not
signed in with a subscription, or that uses an API key, a third-party provider or any
tool other than web search, is rejected and recorded as unavailable.

## Trade Claude's pick

Autotrade follows Codex unless you choose otherwise:

```bash
tradeagent autotrade install --model claude --enable    # follow claude_news
tradeagent autotrade install --enable                   # back to Codex (the default)
```

Everything else is unchanged: the same $1–$5 amount, symbol list, risk limits, one entry
per day and one-hour exit. See [automatic trading](autotrade.md).

## Reading the results

The **GPT vs Claude** page shows, for news and for price-only:

- each model's net return added up over the scored sessions, and on how many sessions
  each did better;
- a verdict on the per-session difference, which waits for 20 sessions and stays
  "no clear difference" until its 95% range excludes zero;
- how often both picked the same stock (on those sessions the difference is zero);
- every session, with both picks and their results. Select one to read both models'
  reasoning side by side.

Treat early numbers as noise. With three candidates a day the two models often agree,
and only the sessions where they disagree can separate them, so a real difference
usually takes months to show.

## Limits

- Claude and Codex search the web with different tools, so the news arms compare each
  model together with its own search.
- The prompt was first written for Codex; both models get the identical text.
- Claude Code updates itself in the background when you use it interactively. Each
  answer records the CLI version and model used, so a change is visible in the records.
- Calls use your subscription's usage limits. If they are exhausted at 10:30, that
  session is unavailable.
