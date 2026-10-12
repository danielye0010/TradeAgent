"""A schema-identical broker version bump blocks new entries, never exits or reads."""

import copy

import pytest

from tradeagent.model import Halt
from tradeagent.schema import Contracts
from tradeagent.standalone_mcp import ReadOnlyMCP, StandaloneMCP


def serve(monkeypatch, version, tools=None):
    tools = copy.deepcopy(tools or Contracts().tools)

    def rpc(self, method, params, **kwargs):
        if method == "initialize":
            return {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "robinhood-trading", "version": version},
            }
        if method == "tools/list":
            return {"tools": [dict(name=n, **t) for n, t in tools.items()]}
        return None

    monkeypatch.setattr(StandaloneMCP, "rpc", rpc)
    return tools


def no_token():
    pytest.fail("credentials requested during catalog validation")


def test_new_entry_session_requires_reviewed_version(monkeypatch):
    serve(monkeypatch, "1.7.4")
    with pytest.raises(Halt, match="server version drift"):
        StandaloneMCP(no_token).__enter__()


def test_exit_only_session_accepts_schema_identical_version_bump(monkeypatch):
    serve(monkeypatch, "1.7.4")
    bridge = StandaloneMCP(no_token)
    bridge.require_server_version = False
    with bridge:
        assert bridge.server_version_reviewed is False


def test_exit_only_session_still_rejects_schema_drift(monkeypatch):
    tools = copy.deepcopy(Contracts().tools)
    schema = tools["place_equity_order"]["inputSchema"]
    schema["additionalProperties"] = not schema.get("additionalProperties", True)
    serve(monkeypatch, "1.7.4", tools)
    bridge = StandaloneMCP(no_token)
    bridge.require_server_version = False
    with pytest.raises(Halt, match="place_equity_order"):
        bridge.__enter__()


def test_read_only_reconciliation_survives_version_bump(monkeypatch):
    serve(monkeypatch, "1.7.4")
    with ReadOnlyMCP(no_token) as bridge:
        assert bridge.server_version_reviewed is False


def test_reviewed_version_is_reported(monkeypatch):
    serve(monkeypatch, Contracts().manifest["server_version"])
    with StandaloneMCP(no_token) as bridge:
        assert bridge.server_version_reviewed is True
