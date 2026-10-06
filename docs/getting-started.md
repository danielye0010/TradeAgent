# Getting Started

## Requirements

- Python 3.12–3.14
- Linux or Ubuntu on WSL2
- Git, Python venv support, and `findmnt` from util-linux

Runtime state must live on a local Linux filesystem because the locking layer uses POSIX file locks. Supported filesystems are ext2/ext3/ext4, btrfs, and xfs. On WSL, use your Linux home directory. Windows mounts, network shares, and tmpfs are not supported for state.

Codex CLI and a Robinhood account are needed only for real-data runs. Installation downloads Python dependencies; the demo runs offline.

## Install

```bash
git clone https://github.com/danielye0010/robinhood-agent-public.git
cd robinhood-agent-public
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
robinhood-agent validate
```

The validation output includes `"valid": true` and `"mode": "SHADOW"`. Run commands from the repository root so the example configs are available.

## Run the demo

```bash
robinhood-agent simulate --demo-dir data/demo
robinhood-agent inspect --config data/demo/inspect.example.json
```

The demo creates one synthetic equity order and one synthetic option order. Repeating the same decision is suppressed. The result includes `"account_and_orders": "SYNTHETIC"` and SQLite integrity `"ok"`.

`data/demo/demonstration.json` contains the summary. The `equity/` and `option/` directories contain separate broker and agent journals. Inspection uses the generated config to show the equity demo's runs and intent.

Use a new directory for each demo, such as `data/demo-2`, and use the same directory in the inspection command. Generated data is ignored by Git.

## Configuration

Copy the examples to local files:

```bash
cp config/config.example.json config/config.local.json
cp config/risk.example.json config/risk.local.json
robinhood-agent validate --config config/config.local.json --risk config/risk.local.json
```

The config sets mode, symbols, strategy version, target fraction, timeout, lease duration, and state directory. The risk config sets cash, exposure, turnover, loss, spread, and data-age limits. Pass both paths on subsequent commands. Local files are ignored by Git.

The other examples cover specific uses: `small-balance-shadow.example.json` selects a single symbol for whole-share sizing; `canary.example.json` configures a canary run; `canary-policy.template.json` shows the signed-policy fields. See [Deployment](deployment.md) for real execution requirements.

## Connect Robinhood

Install [Codex CLI](https://developers.openai.com/codex/cli) in the same Linux environment. You need an eligible Robinhood Agentic Trading account.

```bash
codex login
codex login status
codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading
codex mcp login robinhood-trading
codex mcp get robinhood-trading
robinhood-agent tools
```

Complete authentication in the provider's browser flow. If `robinhood-trading` is already configured, use `codex mcp get` rather than adding it again. Credentials stay outside the project. See the [Codex MCP guide](https://developers.openai.com/codex/extend/mcp) for connection settings.

The native client uses Codex app-server and pinned Robinhood MCP 1.6.2 contracts. Check compatibility when upgrading Codex or the server. `tools` checks the connection and tool schemas; it also initializes the configured local state directory.

## Shadow mode

```bash
robinhood-agent shadow
robinhood-agent inspect
```

Shadow mode reads real account and market data, evaluates the strategy and risk checks, and records hypothetical decisions. It never reviews, places, or cancels orders. No trade or a risk rejection is a normal result.

These commands use the configured state directory, which defaults to `data/`. Demo inspection uses its own generated config.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Missing `venv` or `ensurepip` | Install your distribution's Python venv package. |
| Missing `findmnt` | Install util-linux. |
| Unsupported filesystem | Use a local Linux checkout and a new demo directory. |
| Demo directory already exists | Choose another directory and update the inspection path. |
| Config file not found | Run from the repository root or pass `--config` and `--risk`. |
| Authentication or schema error | Check Codex sign-in, the official endpoint, and server compatibility. |

A completed shadow cycle exits with 0, a safety halt with 2, and an unexpected cycle failure with 1. Read the JSON error before rerunning. See [State and recovery](deployment.md#state-and-recovery) for interrupted orders.
