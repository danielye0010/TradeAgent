# Contributing

Implement focused changes and validate them against real repository files.
Read [Architecture](docs/architecture.md) and [Research protocol](docs/research-protocol.md).

Install `python -m pip install -e ".[dev]"`. Run the checks in [AGENTS.md](AGENTS.md).
Tests and demos must use mocked brokers and fresh synthetic local state.

Preserve immutable evidence and execution correctness. Introduce new strategy versions
rather than changing registered parameters or implementation hashes. A successful
run verifies software; it does not establish alpha or profitability.

Never commit credentials, private keys, account identifiers or runtime state.
Retain upstream attribution and byte-identical vendored sources.
