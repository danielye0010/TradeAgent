# Contributor instructions

TradeAgent is a prospective quantitative laboratory with a separate execution substrate.
Read README, docs/architecture.md and docs/research-protocol.md before changing the loop.

- Strategies receive market snapshots and produce immutable Predictions, never account
  state, broker intents or execution authorization. Keep all enabled versions in shadow.
- Keep exact version/implementation identity, availability timestamps, append-only
  evidence, independent synthetic/replay pools and frozen challenger evaluation rules.
- Preserve losing history, explicit promotion/rejection/retirement and prior-only selector state.
- Daily learning changes statistical state; it does not rewrite source or observations.
- Preserve the execution substrate's durable submission identity, capital bounds,
  locks, fill persistence, recovery and reconciliation. Legacy control workflows are
  optional compatibility code and must not enter the default research path.
- Use mock brokers and new synthetic local Linux state. Never read or modify operator
  runtime state, credentials or signing artifacts during development. No real trading
  without a separate explicit operational request.
- Preserve vendor files, licenses and docs/UPSTREAM_PROVENANCE.json. Regenerate the
  optional legacy manifest after source changes with check_project.py --update-manifest;
  it is not a research trading gate. Preserve unrelated user work.

Validation:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m compileall -q src
python -m build
python -m twine check dist/*
python scripts/check_project.py
tradeagent demo --demo-dir work/fresh-rsi-demo
```

Commit only source and sanitized reproducible artifacts. Never commit runtime DBs,
private reports, authentication files or real-account data. Leave GPT_HANDOFF.md for
substantial changes, pointing to canonical files and observed validation.
