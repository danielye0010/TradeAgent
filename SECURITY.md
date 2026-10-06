# Security

Keep credentials, tokens, cookies, private signing keys, signed account artifacts, and runtime data out of Git. Protect local databases, journals, and backups as account information.

Codex manages native authentication. Standalone mode uses an operator-owned OAuth helper outside the checkout. Private signers remain outside the runtime; it uses public keys to verify signatures.

Stop new entries and preserve state if submission or reconciliation is uncertain. Revoke exposed credentials through the provider's normal controls.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/danielye0010/robinhood-agent-public/security/advisories/new) when available. Otherwise, ask the maintainer for a private reporting channel before sharing sensitive details. There is no separately published private contact address.

Report credential exposure, risk or signature bypasses, duplicate orders, and reconciliation failures privately. Do not include credentials, account details, or exploit steps in public issues.
