# Contributing

Start with the [offline demo](docs/getting-started.md) and [architecture](docs/architecture.md). Use a focused branch from `main`; explain the problem, resulting behavior and validation in your pull request. Preserve unrelated work and keep changes small enough to review.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m compileall -q src
python -m build
python -m twine check dist/*
python scripts/check_project.py
```

Run focused tests first when working on behavior, then the full offline suite. Add or update tests for changed behavior and document user-facing commands. Tests must use mock/synthetic transports and new local state directories; no account credentials or real broker calls belong in CI.

Keep strategy, normalization, deterministic risk, execution policy and reconciliation boundaries separate. Never weaken safety invariants to make a test or trade pass. Release/key enrollment changes require explicit review; simulation artifacts cannot authorize production. See [safety](docs/safety.md).

Do not modify vendored code or remove its MIT notices casually. [THIRD_PARTY.md](THIRD_PARTY.md) records origin and adaptation. Original contributions use [Apache-2.0](LICENSE).

## Bugs and security

Use the [issue tracker](https://github.com/danielye0010/robinhood-agent-public/issues) for ordinary bugs, with version, environment, expected/actual behavior and a synthetic reproducer. Remove account IDs, balances, broker receipts, local paths and credentials from reports. For secrets, safety bypasses or broker-write vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
