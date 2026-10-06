# Contributor instructions

Robinhood Agent connects strategies to the official Trading MCP with risk checks,
signed execution controls, and durable order state. Read README and the four
guides in docs before changing a component.

- Preserve strategy, account eligibility, sizing, risk, execution, reconciliation,
  submission identity, and release behavior unless the task explicitly changes them.
- Use mock brokers and new synthetic state directories for development. Do not
  access a real broker or operator state without an explicit operational request.
- Keep credentials, private keys, signed account artifacts, and runtime files out
  of Git. Preserve vendor code, licenses, and UPSTREAM_PROVENANCE.json.
- Update deployment_manifest.json when deployed files change. Preserve its hash
  and complete-source checks; file changes need matching signed deployment context.
- Keep changes focused, preserve unrelated work, and test changed behavior.

Validation from the repository root:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m compileall -q src
python -m build
python -m twine check dist/*
python scripts/check_project.py
```
