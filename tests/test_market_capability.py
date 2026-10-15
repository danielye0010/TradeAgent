"""Market capability rejects writes while broker validation remains strict."""

import copy
import json
from contextlib import contextmanager
from importlib.resources import files

import pytest

from tradeagent.market_mcp import MARKET_TOOLS, MarketDataMCP
from tradeagent.model import Halt
from tradeagent.research import opportunities
from tradeagent.schema import Contracts
from tradeagent.standalone_mcp import StandaloneMCP


def catalog():
    tools = copy.deepcopy(Contracts().tools)
    tools.update(
        json.loads(files("tradeagent").joinpath("contracts/market-data-1.7.0.json").read_text())[
            "tools"
        ]
    )
    return tools


def transport(monkeypatch, tools):
    calls = []

    def rpc(self, method, params, **kwargs):
        calls.append((method, params))
        if method == "initialize":
            return {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "robinhood-trading", "version": "1.7.3"},
            }
        if method == "tools/list":
            return {"tools": [dict(name=n, **t) for n, t in tools.items()]}
        return None

    monkeypatch.setattr(StandaloneMCP, "rpc", rpc)
    return calls


def test_order_drift_does_not_block_market_but_still_blocks_broker(monkeypatch):
    tools = catalog()
    tools["get_equity_orders"]["inputSchema"]["properties"]["agent_initiated"] = {"type": "string"}
    calls = transport(monkeypatch, tools)
    with MarketDataMCP(lambda: pytest.fail("credentials unexpectedly requested")) as bridge:
        assert set(bridge.contracts.tools) == MARKET_TOOLS
        assert bridge.calls == []
    assert all(m != "tools/call" for m, _ in calls)
    with pytest.raises(Halt, match="get_equity_orders"):
        Contracts().check_current(tools, "1.7.3", require_version=False)


@pytest.mark.parametrize("tool", sorted(MARKET_TOOLS))
@pytest.mark.parametrize("change", ["missing", "input", "output", "read_only"])
def test_market_startup_still_rejects_market_drift(monkeypatch, tool, change):
    tools = catalog()
    if change == "missing":
        del tools[tool]
    elif change == "read_only":
        tools[tool]["annotations"]["readOnlyHint"] = False
    elif change == "input":
        schema = tools[tool]["inputSchema"]
        schema.setdefault("properties", {})["new_mandatory_field"] = {"type": "string"}
        schema["required"] = [*schema.get("required", []), "new_mandatory_field"]
    else:
        properties = tools[tool]["outputSchema"]["properties"]
        del properties[sorted(properties)[0]]
    transport(monkeypatch, tools)
    with pytest.raises(Halt, match=tool):
        MarketDataMCP(lambda: pytest.fail("token requested")).__enter__()


@pytest.mark.parametrize("tool", sorted(set(Contracts().tools) - MARKET_TOOLS))
def test_non_market_calls_rejected_before_network_or_credentials(monkeypatch, tool):
    calls = transport(monkeypatch, catalog())
    bridge = MarketDataMCP(lambda: pytest.fail("token requested"))
    for operation in (
        lambda: bridge.read(tool),
        lambda: bridge.rpc("tools/call", {"name": tool, "arguments": {}}),
    ):
        with pytest.raises(Halt, match="market-data capability"):
            operation()
    assert calls == []


def test_market_rejects_authorization_and_invalid_input_without_network(monkeypatch):
    bridge = MarketDataMCP(lambda: pytest.fail("token requested"))
    with pytest.raises(Halt, match="capability"):
        bridge.rpc("tools/call", {"name": "get_equity_quotes"}, before_send=object())
    bridge.tools = json.loads(
        files("tradeagent").joinpath("contracts/market-data-1.7.0.json").read_text()
    )["tools"]
    with pytest.raises(Halt, match="arguments invalid"):
        bridge.read("get_equity_quotes", {"unexpected": True})


def test_all_31_quotes_batched_with_close_references_and_actual_receipts(monkeypatch):
    from test_opportunities import NOW

    calls = []
    clock = [NOW]

    class Bridge:
        def read(self, name, args):
            calls.append((name, args))
            clock[0] += 0.1
            symbols = args["symbols"]
            if name == "get_equity_historicals":
                assert len(symbols) <= 10
                return {"data": {"results": []}}
            assert name == "get_equity_quotes" and len(symbols) <= 20
            return {
                "data": {
                    "results": [
                        {
                            "quote": {
                                "symbol": symbol,
                                "bid_price": "100",
                                "ask_price": "101",
                                "venue_bid_time": opportunities.iso(NOW),
                                "venue_ask_time": opportunities.iso(NOW),
                                "previous_close_date": "2026-10-07",
                                "previous_close": "99",
                            }
                        }
                        for symbol in symbols
                    ]
                }
            }

    @contextmanager
    def provider(*args):
        class Adapter:
            bridge = Bridge()

        yield Adapter()

    monkeypatch.setattr(opportunities, "open_provider", provider)
    result = opportunities.capture(opportunities.UNIVERSE, clock=lambda: clock[0])
    quotes = [args["symbols"] for name, args in calls if name == "get_equity_quotes"]
    assert [len(batch) for batch in quotes] == [20, 11]
    assert [symbol for batch in quotes for symbol in batch] == opportunities.UNIVERSE
    assert set(result["quotes"]) == set(result["references"]) == set(opportunities.UNIVERSE)
    assert result["quotes"]["AAPL"]["observed_at"] < result["quotes"]["GLD"]["observed_at"]
