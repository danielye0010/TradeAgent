# Security

Never commit OAuth/PAT/API tokens, passwords, cookies, session caches, `.env` secrets, private signing keys or signed real-account artifacts. Runtime databases, journals and order receipts are sensitive even when account identifiers are hashed. Keep them outside Git and protect backups.

Native authentication belongs to Codex and the provider's browser flow. The advanced standalone transport accepts only an operator-owned external OAuth helper; it does not implement login or extract another application's credentials. Public verification keys are distinct from private signers. Never ask an issue reporter to supply credentials.

Treat release bypasses, weakened risk checks, duplicate submission and reconciliation failures as security issues. Stop automation and preserve state after uncertain execution. Credential exposure should also be revoked through the affected provider's normal controls.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/danielye0010/robinhood-agent-public/security/advisories/new) when it is enabled and available to your account. If it is unavailable, request a private reporting channel from the maintainer before sending sensitive details. There is no separately published private contact address. Do not put tokens, account data, live order receipts or exploit details in public issues.

The project is pre-release. Security guidance and tests do not constitute a security audit or a guarantee of safe/profitable trading.
