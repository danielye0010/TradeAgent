"""Claude Code CLI assessor: subscription login only, same prompt as Codex, tool audit."""

import json
import tempfile

import pytest
from test_daily_news import cli_document
from test_opportunities import NOW, market

from tradeagent.prospective import claude_assessor
from tradeagent.prospective.assessor import news_request
from tradeagent.research.opportunities import scan

LOGIN = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}


def init(tools, model=claude_assessor.MODEL, source="none"):
    return {
        "type": "system",
        "subtype": "init",
        "model": model,
        "tools": tools,
        "apiKeySource": source,
    }


def tool(name, uid, result="ok", error=False):
    return [
        {
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": {}}]},
        },
        {
            "type": "user",
            "parent_tool_use_id": None,
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": uid,
                        "content": result,
                        "is_error": error,
                    }
                ]
            },
        },
    ]


def result(document, **extra):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "permission_denials": [],
        "structured_output": document,
        "modelUsage": {claude_assessor.MODEL: {}},
        **extra,
    }


def fake_claude(tmp_path, events, login=LOGIN, code=0):
    """A stand-in `claude` that logs its argv, stdin and environment."""
    log = tmp_path / "calls.jsonl"
    executable = tmp_path / "claude"
    executable.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        f"log = {str(log)!r}\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['auth', 'status']:\n"
        f"    print({json.dumps(json.dumps(login))}); sys.exit(0)\n"
        "if args == ['--version']:\n"
        "    print('2.1.303 (Claude Code)'); sys.exit(0)\n"
        "prompt = sys.stdin.read()\n"
        "with open(log, 'a') as f:\n"
        "    f.write(json.dumps({'args': args, 'prompt': prompt, 'cwd': os.getcwd(),\n"
        "        'env': sorted(os.environ)}) + '\\n')\n"
        f"for line in {json.dumps([json.dumps(e) for e in events])}:\n"
        "    print(line)\n"
        f"sys.exit({code})\n"
    )
    executable.chmod(0o700)
    return executable, log


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def report():
    return scan(market(), now=NOW, candidate_count=1)


def test_news_arm_uses_the_v2_prompt_and_counts_searches(tmp_path, report, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription-token")
    document = cli_document(report)
    events = [
        init(["WebSearch", "StructuredOutput"]),
        *tool("WebSearch", "s1"),
        *tool("WebSearch", "s2"),
        *tool("StructuredOutput", "o1"),
        result(document),
    ]
    executable, log = fake_claude(tmp_path, events)
    answer, provenance = claude_assessor.assess_news(report, executable=str(executable), now=NOW)
    assert answer == document
    assert provenance["web_searches"] == 2 and provenance["web_search_mode"] == "WebSearch"
    assert provenance["model"] == claude_assessor.MODEL
    assert provenance["auth_method"] == "claude.ai"
    assert provenance["claude_code_version"] == "2.1.303"
    (call,) = calls(log)
    prompt, schema = news_request(report, now=NOW, search=True)
    assert call["prompt"] == prompt  # byte-identical to the Codex question
    args = call["args"]
    assert args[args.index("--model") + 1] == claude_assessor.MODEL
    assert args[args.index("--tools") + 1] == "WebSearch"
    assert json.loads(args[args.index("--json-schema") + 1]) == schema
    assert "--no-session-persistence" in args and "--strict-mcp-config" in args
    # Only the empty temporary project's settings: no user hooks, CLAUDE.md or memory.
    assert args[args.index("--setting-sources") + 1] == "project"
    assert call["cwd"].startswith(tempfile.gettempdir())
    # The subscription token stays; API keys never reach the CLI.
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" in call["env"]
    assert "DISABLE_AUTOUPDATER" in call["env"]


def test_price_arm_has_no_tools(tmp_path, report):
    document = cli_document(report)
    events = [init(["StructuredOutput"]), *tool("StructuredOutput", "o1"), result(document)]
    executable, log = fake_claude(tmp_path, events)
    _, provenance = claude_assessor.assess_news(
        report, executable=str(executable), now=NOW, search=False
    )
    assert provenance["web_searches"] == 0 and provenance["web_search_mode"] == "disabled"
    args = calls(log)[0]["args"]
    assert args[args.index("--tools") + 1] == ""
    assert "--allowedTools" not in args


@pytest.mark.parametrize(
    "login, message",
    [
        ({"loggedIn": True, "authMethod": "api_key"}, "subscription"),
        ({"loggedIn": False, "authMethod": "none"}, "subscription"),
        ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"}, "third-party"),
    ],
)
def test_only_a_subscription_login_is_accepted(tmp_path, report, login, message):
    executable, log = fake_claude(tmp_path, [], login=login)
    with pytest.raises(ValueError, match=message):
        claude_assessor.assess_news(report, executable=str(executable), now=NOW)
    assert not log.exists()  # refused before any model call


@pytest.mark.parametrize(
    "events, message",
    [
        ([init(["StructuredOutput", "Bash"])], "unexpected tools"),
        ([init(["StructuredOutput"], model="claude-other")], "different model"),
        ([init(["StructuredOutput"], source="user")], "API key"),
        ([init(["WebSearch", "StructuredOutput"]), *tool("Bash", "b1")], "tool activity"),
        ([init(["WebSearch", "StructuredOutput"]), *tool("WebSearch", "s1", error=True)], "search"),
    ],
)
def test_audit_rejects_anything_unexpected(tmp_path, report, events, message):
    events = [*events, result(cli_document(report))]
    executable, _ = fake_claude(tmp_path, events)
    with pytest.raises(ValueError, match=message):
        claude_assessor.assess_news(report, executable=str(executable), now=NOW)


@pytest.mark.parametrize(
    "extra",
    [
        {"subtype": "error_max_structured_output_retries"},
        {"is_error": True},
        {"permission_denials": [{"tool_name": "WebFetch"}]},
        {"structured_output": None},
    ],
)
def test_a_failed_run_is_not_an_answer(tmp_path, report, extra):
    events = [init(["WebSearch", "StructuredOutput"]), result(cli_document(report), **extra)]
    executable, _ = fake_claude(tmp_path, events)
    with pytest.raises(ValueError, match="failed"):
        claude_assessor.assess_news(report, executable=str(executable), now=NOW)


def test_an_answer_must_cover_the_frozen_shortlist(tmp_path, report):
    document = cli_document(report)
    document["assessments"][0]["symbol"] = "NOTLISTED"
    events = [init(["WebSearch", "StructuredOutput"]), result(document)]
    executable, _ = fake_claude(tmp_path, events)
    with pytest.raises(ValueError, match="frozen shortlist"):
        claude_assessor.assess_news(report, executable=str(executable), now=NOW)


def test_a_nonzero_exit_is_rejected(tmp_path, report):
    executable, _ = fake_claude(tmp_path, [], code=1)
    with pytest.raises(ValueError, match="failed"):
        claude_assessor.assess_news(report, executable=str(executable), now=NOW)


def test_bidirectional_protocols_are_out_of_scope(report):
    with pytest.raises(ValueError, match="daily-news-v2 only"):
        claude_assessor.assess_news({**report, "research_protocol": "daily-call-put-v4"})
