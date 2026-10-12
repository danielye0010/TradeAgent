"""Official market-data capability, independent of account/execution contracts."""

import json
from importlib.resources import files

from .model import Halt, digest
from .standalone_mcp import StandaloneMCP

MARKET_TOOLS = frozenset({"get_equity_historicals", "get_equity_quotes"})


class MarketDataMCP(StandaloneMCP):
    """Two pinned read tools; every other tool is denied before token or HTTP use."""

    def __init__(self, token_source, timeout=20):
        super().__init__(token_source, timeout)
        pin = json.loads(
            files("tradeagent").joinpath("contracts/market-data-1.7.0.json").read_text()
        )
        if set(pin["tools"]) != MARKET_TOOLS:
            raise Halt("market-data contract scope differs from the two-tool capability")
        self.contracts.manifest = pin
        self.contracts.tools = pin["tools"]
        self.contracts.hash = digest(self.contracts.tools)

    def rpc(self, method, params, *, notification=False, before_send=None):
        if before_send is not None or (
            method == "tools/call" and params.get("name") not in MARKET_TOOLS
        ):
            raise Halt("market-data capability forbids account reads, review, place and cancel")
        return super().rpc(method, params, notification=notification)

    def read(self, name, arguments=None):
        if name not in MARKET_TOOLS:
            raise Halt("market-data capability permits only historicals and quotes")
        return super().read(name, arguments)
