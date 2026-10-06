# Contributing

Start with [Getting Started](docs/getting-started.md) and [Architecture](docs/architecture.md). Create a branch from `main`, keep changes focused, and describe what changed and how you tested it.

## Development setup

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

Add tests for changed behavior. Use synthetic brokers and new local state directories; tests and CI need no brokerage credentials. Keep strategy, risk, execution, and reconciliation separate, and preserve existing risk limits and order recovery behavior.

Update docs for user-facing changes. Refresh the deployment manifest when deployed files change. Key enrollment and real execution changes need their own review. Preserve the [third-party licenses](THIRD_PARTY.md).

## Reporting bugs

Open an [issue](https://github.com/danielye0010/tradeagent/issues) with the version, environment, expected result, and a synthetic reproducer. Remove credentials, account details, order receipts, and personal paths. Follow [Security](SECURITY.md) for sensitive issues.

After changing source, tests, public configs, dependencies, or licenses, regenerate
`docs/deployment_manifest.json` with `python scripts/check_project.py --update-manifest`.
The deployment hash changes; real deployments need a matching signed policy.
