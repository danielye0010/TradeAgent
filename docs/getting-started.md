# Getting started

Start with the synthetic demo. It exercises the same local journal and execution lifecycle without Codex, a Robinhood account, credentials, or real money.

## Environment

Use Linux or Ubuntu on WSL2. State uses POSIX process locks and `findmnt` from util-linux. The runtime supports local ext2/ext3/ext4, btrfs, or xfs filesystems. On WSL, clone under your Linux home directory, not a Windows-mounted or network drive. `/tmp` may be tmpfs and is unsuitable for execution state.

Package metadata requires Python 3.11 or newer. Local validation uses Python 3.14; the CI workflow tests that version on Linux. Older eligible interpreters are not claimed as tested. Native Windows and macOS execution are not supported by the state layer.

Install Python's venv support if your distribution packages it separately. Git and internet access are needed for installation; runtime simulation itself is offline.

## Install

From a new checkout:

```bash
git clone https://github.com/danielye0010/robinhood-agent-public.git
cd robinhood-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
robinhood-agent validate
```

The repository is private until separately released; cloning requires permission. `validate` checks local config structure, not account access or permission to trade. The JSON response includes `"valid": true` and `"mode": "SHADOW"`.

There is no PyPI release. Use the source checkout so public configs and deployment metadata remain available. The import package is `robinhood_agent`; the command is `robinhood-agent`.

## Run and inspect the synthetic demo

```bash
robinhood-agent simulate --demo-dir data/demo
robinhood-agent inspect --config data/demo/inspect.example.json
```

Expected results:

- `account_and_orders` is `SYNTHETIC` and `real_review_place_cancel_calls` is zero.
- Both equity and option fixtures report one synthetic broker order and SQLite integrity `ok`.
- A repeated decision is suppressed rather than submitted twice.
- The inspection shows the equity demo runs and intent, rather than opening the default real-data journal.

Artifacts live under `data/demo`: `demonstration.json`, an inspection config, and separate `equity` and `option` broker/agent journals. They are private local runtime files and ignored by Git. The option fixture proves only simulated infrastructure; it does not enable live options.

If `data/demo` exists, choose a new name, such as `data/demo-2`, and use that name for both commands. Do not delete existing state just to repeat a demonstration.

## Configuration

`config/config.example.json` sets SHADOW mode, symbols, strategy version, target fraction, timeout, lease duration, and state directory. `config/risk.example.json` defines deterministic exposure, cash, turnover, loss, spread, and freshness limits. Examples contain no credentials or account IDs.

For local settings, copy the examples to ignored paths:

```bash
cp config/config.example.json config/config.local.json
cp config/risk.example.json config/risk.local.json
robinhood-agent validate --config config/config.local.json --risk config/risk.local.json
```

Use these paths explicitly on later commands. Changing `mode` or a strategy does not enroll a key or grant real execution. Keep SHADOW for onboarding. `small-balance-shadow.example.json` is a read-only single-symbol sizing fixture; `canary.example.json` and `canary-policy.template.json` serve deployment tests and review, not the no-account quickstart. The policy template is unsigned and expired by construction.

## Connect the official MCP

This separate path accesses real account and market data. It requires an eligible Robinhood Agentic Trading account and a working Codex CLI in the same Linux environment. The native bridge was developed against Codex CLI 0.160.1 and pins official MCP server 1.6.2 contracts; compatibility with newer versions must be checked, not assumed.

Install Codex using its [official instructions](https://developers.openai.com/codex/cli). Then use the native browser authentication flow:

```bash
codex login
codex login status
codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading
codex mcp login robinhood-trading
codex mcp get robinhood-trading
robinhood-agent tools
```

If the named server is already configured, inspect it with `codex mcp get` instead of adding it again. Complete sign-in directly in the provider's browser flow. Do not paste tokens into project files, terminal commands, or issue reports. See [official MCP configuration guidance](https://developers.openai.com/codex/extend/mcp).

`tools` checks authentication, official endpoint, tool annotations and pinned contracts. It can initialize local state at the configured state directory. Run it only against the state location intended for your own checkout. A failed contract or account check is a safety halt, not a reason to bypass validation.

When you deliberately want one real-data SHADOW evaluation:

```bash
robinhood-agent shadow
robinhood-agent inspect
```

SHADOW makes broker reads and records hypothetical decisions; it never reviews, places, or cancels orders. A rejected or absent proposal is a valid result. These two commands refer to the configured SHADOW journal, whereas demo inspection uses its generated config.

## Common setup problems

| Symptom | Action |
| --- | --- |
| Missing `venv` or `ensurepip` | Install the distribution's Python venv package, then create the environment again. |
| Missing `findmnt` | Install util-linux. |
| Unsupported filesystem | Move a new checkout and its new demo to a supported Linux filesystem; preserve existing runtime data. |
| Demo directory already exists | Choose a new demo directory and inspect that directory's generated config. |
| Config missing | Run from the repository root or pass explicit `--config` and `--risk` paths. |
| Authentication/contract halt | Verify native sign-in and official server compatibility; never copy another application's tokens. |
| CLI exits with code 2 | Read the JSON halt reason and correct the cause without weakening risk or release controls. |

The SHADOW command returns 0 for a completed cycle, 2 for a safety halt, and 1 for an unexpected cycle failure. Do not automate retries on unknown submission state. Continue with [deployment and recovery](deployment.md).
