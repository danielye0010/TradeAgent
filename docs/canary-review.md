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

Official completed regular-session daily bars plus SPY benchmark and current
executable quotes feed the unchanged research strategy formulas/scan/ranking.
RSI here names the research loop, not a technical RSI oscillator; no oscillator,
period or new strategy was added. The live adapter's frequency is now daily as
specified for this timestamp repair. Strategy parameters and numerical feature
formulas are unchanged.

The provider's original daily `begins_at` is retained in raw history evidence and
as the final signal label. Its normalized start identifies the same bar; end is
the mapped XNYS session's official close, including early closes. Supported labels
are midnight UTC (a date label), New York midnight or exact regular-session open.
Unknown times and non-session dates fail closed; no neighbouring session is guessed.
Today's bar is excluded before official close, even when labelled midnight UTC.
After close it can be used only if the provider actually supplies it. Future,
duplicate, unordered and explicitly interpolated history is rejected or excluded.
No wall-clock timestamp replaces a provider label or bar completion time.

Daily signal snapshots carry `signal_bar_begins_at`. Bar completion <= actual
history receipt <= actual observation/decision time. Current quote receipt/time
and all broker quote/book freshness gates remain independent of historical-bar age.
The old exact-end-at-decision rule remains for existing intraday snapshots.
Daily signal-bar identity uses source/pool/symbol/benchmark and canonical final
bar starts/session close. The existing experience payload is queried before
prediction, so later observations of the same signal bar suppress duplicates.
No database schema, old evidence or immutable version record is rewritten.

Changing snapshot validation changes the existing composite strategy hash. The scan
accepts precisely the reviewed old-to-new timing-only hash pair; no arbitrary old
or future implementation hash is accepted. Numerical strategy/feature sources,
parameters, ranking and prior state are unchanged. A further domain/strategy/feature
edit invalidates the compatibility pair. This is no execution authorization.

Unavailable session reference fields still cause the existing strategy families
to abstain. The existing classification/reference reader and canary/risk gates
are reused without weakening their rules.

After at most one review there are no further broker calls. The report preserves
observed market data, RSI evidence, unchanged selector decisions, gates and warnings.
A readiness report means a separately user-confirmed **personal Robinhood** transaction,
not permission for programmatic execution. Review expires at request start + 30 seconds
and is never extended or automatically refreshed. An expired report is not actionable.
The existing order-review response does not echo TIF/hours; those stay bound to the exact
validated GFD/regular-hours request. Unknown/missing quantity or limit echo fails closed.
