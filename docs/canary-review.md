# Review-only live CANARY

`tradeagent canary-review` is an explicit one-shot, non-placing action. It uses the
frozen RSI population/scan/rank and underlying expression, followed by the unchanged
one-share infrastructure selector and risk checks. A negative/rejected RSI signal
or disagreement with the unchanged canary selection returns NO_TRADE. It never
reorders candidates or forces a signal to exercise the broker.

```bash
tradeagent canary-review \
  --oauth-helper "$HOME/.local/libexec/robinhood-mcp-oauth-helper" \
  --account-digest "$DEDICATED_ACCOUNT_DIGEST" \
  --research-state data/research \
  --output "$HOME/.local/state/robinhood-agent/unique-canary-review"
```

Run from the reviewed source checkout during actual XNYS regular hours. The output
must be a new private local Linux directory; an existing output is rejected rather
than replayed. OAuth stays in the external helper/Codex stores. No account number,
credential, signing key or private operational evidence belongs in Git.

Complete official metadata is discovered with metadata-only direct MCP; broker
reads and the existing `CodexBridge.review_equity_once` run through the authenticated
Codex MCP connection. The Codex tool allowlist contains existing reads plus review,
never placement/cancel tools. Server identities, origin and structural schemas agree
across both connections; the exact reviewed 1.7.0 pin remains mandatory. Unknown
version/schema/annotation changes halt before broker preparation.

The review subclass denies every execution operation both before exchange and at
actual send, including direct RPC/send attempts. The existing whole-share/GFD/
regular-hours/BUY/three-dollar review restriction remains. The exact selected payload
is bound before review; the one-use send allowance is consumed before I/O. Timeout,
malformed response, warnings, wrong echo, price disagreement or expiry halt without
retry. No execution adapter, submitting intent, side budget, grant, enrollment,
placement, cancellation, replacement or automatic exit exists in this workflow.
The production and human execution-key gates remain unchanged in all other paths.

The fresh review journal uses the existing SQLite/process-lock/fenced-lease machinery
for startup recovery, accounting and audit events, but creates no broker intents or
submission references. RSI snapshots/predictions/ranks/plans use the separate normal
Experience store. Existing strategy versions and prior prospective scores are retained;
only an empty store receives the unchanged defaults registered at the actual current
time, never retroactively. No learning, evolution or promotion runs here.

Official five-minute bars plus SPY benchmark and executable quotes feed the frozen
MarketSnapshot/scan checks. Availability is the observed response receipt. Completed
bar ends are derived from the advertised interval, never relabeled as receipt time;
quotes/bars are never backdated. The frozen RSI requires completed bars ending at the
exact decision and availability no later than that decision. If this API cannot
supply that contemporaneous alignment, the action HALTs; it does not substitute
legacy EMA, historical replay, synthetic data, a shortened bar or a forced proposal.
Unavailable session reference fields cause the existing strategy families to abstain.
The existing classification/reference reader is reused without weakening its rules.

After at most one review there are no further broker calls. The report preserves
observed market data, RSI evidence, unchanged selector decisions, gates and warnings.
A readiness report means a separately user-confirmed **personal Robinhood** transaction,
not permission for programmatic execution. Review expires at request start + 30 seconds
and is never extended or automatically refreshed. An expired report is not actionable.
The existing order-review response does not echo TIF/hours; those stay bound to the exact
validated GFD/regular-hours request. Unknown/missing quantity or limit echo fails closed.
