"""Bounded subscription Claude Code CLI assessment for the Claude comparison arms.

The same prompt and output schema as the daily-news-v2 Codex assessor
(:func:`assessor.news_request`); only the model and the CLI differ. The CLI runs
non-interactively (``claude -p``) on the owner's Claude subscription login, never an
API key: environment API credentials are removed and the login method is checked
before every call. Web search is the only tool that can be enabled, and only for the
news arm; any other tool use, permission denial or search failure rejects the answer.
"""

import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path

from ..research.domain import identity
from .assessor import check_document, news_request

MODEL = "claude-opus-5-5"
EFFORT = "low"
SEARCH_TOOL = "WebSearch"
# Claude Code returns schema-validated output through this built-in tool.
OUTPUT_TOOL = "StructuredOutput"
# `claude auth status` methods that bill a Claude subscription: a /login session or a
# `claude setup-token` token. API keys, key helpers and cloud providers are refused.
SUBSCRIPTION = {"claude.ai", "oauth_token"}
EVENTS_LIMIT = 4_000_000


def environment():
    """Inherited environment without API credentials, provider switches or parent-session state."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("ANTHROPIC_")
        and not (k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_OAUTH_TOKEN")
        and k not in {"CLAUDECODE", "OPENAI_API_KEY"}
    }
    # A mid-experiment self-update would silently change the assessor.
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def _output(command, env, timeout):
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Claude Code CLI is not installed or did not respond") from None
    return result


def login(executable="claude", env=None, timeout=30):
    """The subscription login method; no account identifiers are returned or stored."""
    env = environment() if env is None else env
    result = _output([executable, "auth", "status"], env, timeout)
    try:
        status = json.loads(result.stdout)
    except ValueError:
        raise ValueError("Claude Code login status unavailable") from None
    method = status.get("authMethod")
    if not status.get("loggedIn") or method not in SUBSCRIPTION:
        raise ValueError("Claude Code is not signed in with a Claude subscription (run `claude`)")
    if status.get("apiProvider", "firstParty") != "firstParty":
        raise ValueError("Claude Code is configured for a third-party API provider")
    return method


def version(executable="claude", env=None, timeout=30):
    env = environment() if env is None else env
    result = _output([executable, "--version"], env, timeout)
    words = result.stdout.split()
    if result.returncode or not words:
        raise ValueError("Claude Code CLI is not installed or did not respond")
    return words[0]


def command(executable, schema, search):
    return [
        executable,
        "-p",
        "--model",
        MODEL,
        "--effort",
        EFFORT,
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--tools",
        SEARCH_TOOL if search else "",
        *(["--allowedTools", SEARCH_TOOL] if search else []),
        "--disallowedTools",
        "mcp__*",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        # The working directory is an empty temporary folder; loading only its
        # (absent) project settings keeps user hooks, CLAUDE.md and memory out.
        "--setting-sources",
        "project",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]


def assess_news(report, *, executable="claude", timeout=300, now=None, search=True):
    """Claude's answer to the daily-news-v2 prompt for the same frozen shortlist."""
    if report.get("research_protocol") in {"daily-bidirectional-v3", "daily-call-put-v4"}:
        raise ValueError("the Claude comparison follows daily-news-v2 only")
    candidates = report["candidates"][:3]
    mode = SEARCH_TOOL if search else "disabled"
    if not candidates:
        return {"assessments": []}, {
            "model": MODEL,
            "status": "VALID",
            "tool_calls": 0,
            "web_searches": 0,
            "web_search_mode": mode,
        }
    prompt, schema = news_request(report, now=now, search=search)
    env = environment()
    method = login(executable, env)
    cli_version = version(executable, env)
    allowed = {OUTPUT_TOOL, *([SEARCH_TOOL] if search else [])}
    with tempfile.TemporaryDirectory(prefix="tradeagent-claude-") as temporary:
        events = Path(temporary) / "events.jsonl"
        with events.open("wb") as stream:
            process = subprocess.Popen(
                command(executable, schema, search),
                cwd=temporary,
                stdin=subprocess.PIPE,
                stdout=stream,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            try:
                process.communicate(prompt.encode(), timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise ValueError("Claude assessment deadline exceeded") from None
        if events.stat().st_size > EVENTS_LIMIT:
            raise ValueError("Claude assessment output exceeds bound")
        text = events.read_text()
    if process.returncode:
        raise ValueError("Claude noninteractive assessment failed; no valid Agent decision")
    try:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError:
        raise ValueError("Claude CLI emitted malformed events") from None
    document, audit = audit_events(rows, allowed)
    check_document(document, schema, candidates, name="Claude")
    return document, {
        "model": MODEL,
        "effort": EFFORT,
        "status": "VALID",
        "tool_calls": audit["searches"],
        "web_searches": audit["searches"],
        "web_search_mode": mode,
        "num_turns": audit["turns"],
        "models_used": audit["models"],
        "claude_code_version": cli_version,
        "auth_method": method,
        "prompt_hash": identity(prompt),
        "output_hash": identity(document),
    }


def audit_events(rows, allowed):
    """Reject anything but the configured model, permitted tools and one clean result."""
    inits = [r for r in rows if r.get("type") == "system" and r.get("subtype") == "init"]
    if len(inits) != 1:
        raise ValueError("Claude CLI session metadata missing")
    init = inits[0]
    if init.get("model") != MODEL:
        raise ValueError("Claude CLI ran a different model; assessment rejected")
    if set(init.get("tools") or []) - allowed:
        raise ValueError("Claude CLI exposed unexpected tools; assessment rejected")
    if init.get("apiKeySource", "none") != "none":
        raise ValueError("Claude CLI used an API key instead of the subscription login")
    searches, failed = set(), set()
    for row in rows:
        if row.get("type") not in {"assistant", "user"}:
            continue
        if row.get("parent_tool_use_id"):
            raise ValueError("unexpected Claude subagent activity; assessment rejected")
        content = (row.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"tool_use", "server_tool_use"}:
                if block.get("name") not in allowed:
                    raise ValueError("unexpected Claude tool activity; assessment rejected")
                if block.get("name") == SEARCH_TOOL:
                    searches.add(block.get("id"))
            elif block.get("type") == "tool_result" and block.get("is_error"):
                failed.add(block.get("tool_use_id"))
    if searches & failed:
        raise ValueError("Claude web search failed; assessment rejected")
    if failed:
        raise ValueError("Claude tool call failed; assessment rejected")
    results = [r for r in rows if r.get("type") == "result"]
    if not results:
        raise ValueError("Claude noninteractive assessment failed; no valid Agent decision")
    result = results[-1]
    if (
        result.get("subtype") != "success"
        or result.get("is_error")
        or result.get("permission_denials")
        or not isinstance(result.get("structured_output"), dict)
    ):
        raise ValueError("Claude noninteractive assessment failed; no valid Agent decision")
    return result["structured_output"], {
        "searches": len(searches),
        "turns": result.get("num_turns"),
        "models": sorted(result.get("modelUsage") or {}),
    }
