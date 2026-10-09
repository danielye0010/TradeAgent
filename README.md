# TradeAgent

TradeAgent is a Python trading research project with scheduled SHADOW comparisons,
Codex-assisted market analysis, and a separate engine for manually authorized LIVE
execution through the official Robinhood Trading MCP.

[![CI](https://github.com/danielye0010/TradeAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/danielye0010/TradeAgent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12%E2%80%933.14-blue)
[![License](https://img.shields.io/badge/License-Apache--2.0%20AND%20MIT-blue)](LICENSE)

[Quick start](#quick-start) · [Research](#research) · [Scheduled SHADOW](#scheduled-shadow) · [LIVE execution](#live-execution) · [Docs](#documentation)

**v1.0.0-beta.1 is a pre-release. Prospective profitability and Codex uplift are
not established.** Synthetic tests and historical results validate specific software
behavior and research assumptions; they do not establish a repeatable trading edge.

## Quick start

Supported: Linux with Python 3.12–3.14, including Linux under WSL2. Native Windows
and macOS are not supported. Scheduling requires systemd user services.

Download the wheel and `SHA256SUMS` from the
[GitHub pre-release](https://github.com/danielye0010/TradeAgent/releases/tag/v1.0.0-beta.1).
Verify the wheel's SHA-256 against `SHA256SUMS`, then install in a fresh environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./tradeagent-1.0.0b1-py3-none-any.whl

# Synthetic research and a simulated broker lifecycle; no accounts needed
tradeagent demo --demo-dir work/demo
tradeagent inspect --state-dir work/demo
tradeagent run-once --paper --state-dir work/paper
```

Use fresh demo directories. Runtime storage requires a local Linux ext2/ext3/ext4,
btrfs or xfs filesystem; network shares, Windows mounts and tmpfs are rejected. Releases
are distributed through GitHub; this release is not published to PyPI.

## Research

The scanner ranks price, volume and relative-strength anomalies across 31 US stocks
and ETFs. Codex assessments and quantitative signals are stored before their outcomes
are observed. Decisions retain source, strategy identity, costs and entry/exit windows.
Missing or insufficient evidence can produce `NO_TRADE` or an incomplete result.

Market reads use an owner-managed external OAuth helper for the official
[Robinhood Trading MCP](https://robinhood.com/us/en/support/articles/agentic-trading-overview/).
TradeAgent does not perform login or store tokens. See the
[helper contract](docs/prospective-shadow.md#robinhood-setup). The manual scanner
also supports Alpaca data with `scan --provider alpaca`; feeds remain separate.

```bash
tradeagent opportunity scan --candidates 3
tradeagent opportunity evidence --template > work/assessment.json
# Complete the assessment using the saved market evidence before importing it
tradeagent opportunity assess --input work/assessment.json
tradeagent opportunity decide --refresh
tradeagent opportunity show
tradeagent opportunity compare
```

The repository's `trade-opportunity-analyst` Codex skill can perform this manual
workflow. Its default is research. These commands do not authorize broker orders.
See [Opportunity workflow](docs/opportunity-workflow.md) for evidence requirements
and resolution against subsequent market observations.

Three frozen intraday hypotheses test opening continuation, stabilized reversal
and beta-adjusted residual strength. The daily comparison uses a five-minute entry
delay and a one-hour hold. Modeled spread, slippage and fees are included; bearish
signals are recorded without short execution. [Alpha findings](docs/alpha-findings.md)
describe the historical evidence and its limits.

## Scheduled SHADOW

The daily systemd timer records four arms: Quant Only, Codex Only, Quant + Codex and
seeded Random with a cash alternative. Codex receives supplied market evidence;
the scheduled assessor does not browse news or use broker tools.

| Time (New York) | Scheduled research |
| --- | --- |
| 10:00 | Capture market data and freeze the session's comparison before 10:03 |
| 11:30 | Attempt resolution of matured modeled outcomes |
| 17:00 | Resolve available paths and update research status |

The timer uses the XNYS calendar and preserves missed windows and unavailable data.
`daily-status.json` separates evidence pools, paired results and coverage. Outcomes
are modeled returns, not broker fills. **The scheduled worker never reviews, places
or cancels orders, and never promotes a research selection into LIVE execution.**

For a new installation, clone the release tag and use the installed wheel interpreter:

```bash
git clone --branch v1.0.0-beta.1 https://github.com/danielye0010/TradeAgent.git
cd TradeAgent
python scripts/install_shadow.py --daily \
  --python /absolute/linux/venv/bin/python \
  --state-dir /absolute/linux/daily-research
```

This installs and enables a user timer. It requires the external Robinhood helper,
an authenticated compatible Codex CLI, and a running host with network access.
Read [SHADOW setup](docs/prospective-shadow.md) before installing or changing a service.

## LIVE execution

LIVE requires an explicit owner request for the current invocation, a private local
TOML configuration, external authentication and current broker permissions. Account,
sizing, risk, freshness and reconciliation checks remain independent of Codex.
The engine manages a foreground entry/exit lifecycle with durable order identity
and owner-invoked recovery. Exits and fills depend on broker, market and host availability.

| Workflow | Behavior |
| --- | --- |
| Research / scheduled SHADOW | Records forecasts and modeled outcomes; no orders |
| Evidence-gated LIVE | Requires prior comparable economic evidence and all execution checks; passing the screen is not proof of profitability |
| Explicit experimental LIVE | A separately requested policy for unvalidated signals, using owner limits and at most one new entry attempt per New York day; never an automatic fallback |

See [Live execution](docs/one-shot.md) for configuration, read-only readiness, kill
switch behavior and recovery, and [Opportunity workflow](docs/opportunity-workflow.md)
for the plan-to-execution boundary. A source installation or scheduled timer does
not authorize real trading. US long equity/ETF execution is supported; options and
short selling are not supported by the owner LIVE path.

## Project layout

```text
.agents/skills/trade-opportunity-analyst/   manual Codex research skill (source checkout)
src/tradeagent/
  research/       forecasts, signals, TradePlans, outcomes and learning
  prospective/    market-data collection and scheduled SHADOW comparisons
  oneshot.py      owner-authorized entry, exit and recovery
  risk.py         deterministic position, exposure and cash limits
  legacy/         earlier execution and compatibility workflows
scripts/          installation and project/release checks
tests/            synthetic markets and mocked brokers
docs/             architecture and operating guides
```

The wheel includes the CLI, contracts and owner configuration example. The source
distribution and tag also include scripts, tests and documentation; the Codex skill
is available in the Git checkout.

## Documentation

- [Opportunity workflow](docs/opportunity-workflow.md)
- [Live execution](docs/one-shot.md)
- [SHADOW setup and scheduling](docs/prospective-shadow.md)
- [Architecture](docs/architecture.md) and [Research protocol](docs/research-protocol.md)
- [Changelog](CHANGELOG.md) and [Release workflow](docs/releasing.md)
- [Contributing](CONTRIBUTING.md) and [Security](SECURITY.md)

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
```

CI validates Python 3.12–3.14 on Linux and checks packaging and an installed-wheel
synthetic smoke test. Use mocked brokers and fresh local state for development.

## License

[Apache 2.0](LICENSE), with vendored MIT components ([details](THIRD_PARTY.md)).
TradeAgent is independent of Robinhood Markets, Inc. Trading involves risk of loss.
