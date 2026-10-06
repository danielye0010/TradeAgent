# Safety invariants

These are execution requirements, not strategy preferences.

- **Risk is authoritative.** Strategy and AI output may propose a decision; deterministic risk, account checks and execution policy decide whether it is admissible.
- **Unknown means halt.** Unknown account type, stale or contradictory data, schema drift, unexpected receipts and unavailable required evidence fail closed.
- **No replay after ambiguous submission.** Timeouts, lost acknowledgements and interrupted sends preserve durable identity. Reconcile existing broker orders before any continuation; never treat an acknowledgement as a fill.
- **Release is deliberate.** Ordinary symbol, strategy or config changes cannot enroll a production key, sign a policy or widen its release phase. Simulation approvals are rejected by production verifiers.
- **Capital and market checks remain independent.** Eligibility, unleveraged cash, reserves, concentration, portfolio exposure, daily turnover/loss, regular session, quote/book freshness and spread constraints must all pass.
- **State is part of safety.** Keep authoritative SQLite, export journal, process lock and fenced lease together on supported persistent local Linux storage. Never delete state or shorten a lease to bypass a halt.
- **Credentials are external.** Native sign-in belongs to Codex/provider authentication. Standalone OAuth refresh belongs to an operator-owned helper outside the checkout. Never place secrets, signed account artifacts, session caches or runtime records in Git.
- **Stages have distinct meanings.** Simulation is synthetic; SHADOW reads real data without writes; real execution requires reviewed deployment controls and a valid signed envelope.

A one-share canary may use its explicitly bounded available-source corporate-action checks. This exception does not relax unknown instrument classification or limited-equity production coverage. Options fixtures do not establish live options safety.

Review [deployment and recovery](deployment.md) before operating. A successful unit test, demo or freeze check proves neither investment performance nor authorization to trade. Report suspected vulnerabilities through [SECURITY.md](../SECURITY.md).
