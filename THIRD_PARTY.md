# Reused upstream source

Original project code is licensed under Apache-2.0. The components below retain
their MIT licenses, preserved verbatim in `licenses/`. Captured revisions and
file hashes are recorded in `docs/UPSTREAM_PROVENANCE.json`. Distribution must
retain the root license and all three upstream notices.

| Upstream and captured revision | Vendored component | Adaptation |
| --- | --- | --- |
| [Oft3r/agentic-trading-desk](https://github.com/Oft3r/agentic-trading-desk/tree/908125fe97b94fa80c14953800656ca8d645f925) | `scripts/indicators.py:ema_series` -> `vendor/indicators.py`; `scripts/score.py:score_trend` -> `vendor/trend.py` | Exact function bodies/AST; module headers/imports narrowed. No upstream autonomous mandate or personal trading rules. License: `licenses/Oft3r-MIT.txt`. |
| [abiemann/RobinhoodEquityTradingAgent](https://github.com/abiemann/RobinhoodEquityTradingAgent/tree/21509ebce1701b115cd4e25846302d19c87b75f6) | `scripts/run_lock.py` -> `vendor/run_lock.py` | Exact bytes, unchanged. Only acquire/renew/release used. Its docstring mentions an upstream schedule; that schedule is not installed here. License: `licenses/abiemann-MIT.txt`. |
| [cbangera2/robinhood-mcp-cli](https://github.com/cbangera2/robinhood-mcp-cli/tree/82a7abdb8286c3a9d144ea624eb43cabf37c8272) | `rh_mcp_cli/client.py:_parse_result` -> `vendor/client.py`; `rh_mcp_cli/exceptions.py` -> `vendor/exceptions.py` | Parser now rejects MCP `isError` before accepting content. Error classes exact bytes. HTTP client and token-storage code were examined during the partial build but removed from the final project. License: `licenses/cbangera2-MIT.txt`. |
