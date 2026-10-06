# Robinhood Agent contributor instructions

Build small, reviewable improvements to official-MCP trading infrastructure. Read
`README.md` and `docs/deployment.md` for purpose and current release state;
`docs/architecture.md` and `docs/safety.md` define durable boundaries.

- Strategy proposes; deterministic risk and signed execution policy authorize.
- Preserve account eligibility, sizing, risk, duplicate protection, reconciliation,
  submission identity, release gates and externally owned signing keys.
- No broker read/review/place/cancel or real trading without a separate explicit
  operational request. Documentation, packaging and tests need no broker access.
- Never open, copy, reset or delete an operator's runtime DB, journal, lock or lease
  for development. Use new synthetic directories on local Linux storage.
- Never collect or commit credentials, session caches, private keys, signed real
  account artifacts or runtime data. Authentication remains outside the checkout.
- Keep `docs/FRAMEWORK_FREEZE.json`: policy checks its reviewed file hashes. A source
  change invalidates prior signed deployment context; do not rebaseline to evade a halt.
- Preserve third-party notices and `docs/UPSTREAM_PROVENANCE.json`. Do not edit
  vendored files without reviewing attribution and verifying provenance.
- Preserve unrelated work. Avoid duplicate simulators, status reports and tooling.
  Update canonical docs and add focused tests for behavioral changes.

Local checks, with the development environment activated:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m compileall -q src
python -m build
python -m twine check dist/*
python scripts/check_project.py
```

Use the existing synthetic demo for onboarding. Tests must mock broker transports.
Public release needs a separate history/privacy review and visibility approval.
