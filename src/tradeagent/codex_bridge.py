"""Read-only native Codex MCP bridge. Never reads authentication files."""

import json
import os
import selectors
import subprocess
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from .model import Halt
from .vendor.client import _parse_result

SERVER = "robinhood-trading"
ENDPOINT = "https://agent.robinhood.com/mcp/trading"
READ_TOOLS = frozenset(
    {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_equity_quotes",
        "get_equity_historicals",
        "get_equity_tradability",
        "get_trade_approval_setting",
        "get_trade_approvals",
        "get_option_chains",
        "get_option_instruments",
        "get_option_quotes",
        "get_option_positions",
        "get_option_orders",
        "get_equity_price_book",
        "search",
    }
)


class CodexBridge:
    def __init__(self, cwd: Path, timeout=60, metadata_tools=(), *, allow_equity_review=False):
        self.cwd, self.timeout = cwd.resolve(), timeout
        self.proc = None
        self.serial = 0
        self.buffer = b""
        self.tools = {}
        self.calls = []
        self.metadata_tools = tuple(metadata_tools)
        self.allow_equity_review = allow_equity_review is True
        self.equity_review_consumed = False

    def __enter__(self):
        cfg = tomllib.loads((Path.home() / ".codex/config.toml").read_text())
        server = cfg.get("mcp_servers", {}).get(SERVER, {})
        if server.get("url") != ENDPOINT or server.get("enabled") is False:
            raise Halt("official Robinhood MCP is not configured")
        allowed = server.get("enabled_tools")
        if not allowed or not set(allowed) <= READ_TOOLS:
            raise Halt("Codex MCP must have an explicit read-only tool allowlist")
        self.proc = subprocess.Popen(
            [
                str(Path.home() / ".local/bin/codex"),
                "app-server",
                "--stdio",
                "-c",
                "mcp_servers.robinhood-trading.enabled_tools="
                + json.dumps(
                    sorted(
                        READ_TOOLS
                        | set(self.metadata_tools)
                        | ({"review_equity_order"} if self.allow_equity_review else set())
                    )
                ),
            ],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.rpc(
                "initialize",
                {
                    "clientInfo": {"name": "robinhood_shadow", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.send({"method": "initialized", "params": {}})
            result = self.rpc(
                "thread/start",
                {
                    "cwd": str(self.cwd),
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                },
            )
            self.thread_id = result["thread"]["id"]
            statuses = self.rpc(
                "mcpServerStatus/list",
                {
                    "threadId": self.thread_id,
                    "serverName": SERVER,
                    "detail": "toolsAndAuthOnly",
                },
            )
            self.inventory = statuses
            for status in statuses["data"]:
                if status["name"] == SERVER:
                    if (
                        status.get("httpOrigin") != "https://agent.robinhood.com"
                        or status.get("runtimeStatus") != "connected"
                        or status.get("toolsError")
                    ):
                        raise Halt("official MCP origin/connection is not verified")
                    self.auth_status = status.get("authStatus")
                    self.tools = status.get("tools", {})
            if "get_accounts" not in self.tools:
                # Some versions key by qualified tool name.
                self.tools = {v["name"]: v for v in self.tools.values()}
            if "get_accounts" not in self.tools:
                raise Halt("authenticated account-read tool unavailable")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.proc = None

    def send(self, message):
        raw = (json.dumps(message, allow_nan=False) + "\n").encode()
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def receive(self, deadline):
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Halt("Codex MCP request timed out; no automatic retry")
            with selectors.DefaultSelector() as selector:
                selector.register(self.proc.stdout, selectors.EVENT_READ)
                if not selector.select(remaining):
                    raise Halt("Codex MCP request timed out; no automatic retry")
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise Halt("Codex app-server disconnected")
            self.buffer += chunk
            if len(self.buffer) > 32 * 1024 * 1024:
                raise Halt("oversized MCP response")
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def rpc(self, method, params):
        if method not in {
            "initialize",
            "thread/start",
            "mcpServerStatus/list",
            "mcpServer/tool/call",
        }:
            raise Halt("protocol method is outside the read-only project capability")
        if method == "mcpServer/tool/call":
            name = params.get("tool")
            if params.get("server") != SERVER or name not in READ_TOOLS or name not in self.tools:
                raise Halt("broker operation is outside the read-only capability")
            if self.tools[name].get("annotations", {}).get("readOnlyHint") is not True:
                raise Halt("broker tool does not advertise read-only behavior")
        return self._exchange(method, params)

    def _exchange(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            msg = self.receive(deadline)
            if "method" in msg:
                if "id" in msg:
                    # Any auth/approval/elicitation boundary requires the human.
                    self.send(
                        {
                            "id": msg["id"],
                            "error": {
                                "code": -32601,
                                "message": "interactive action requires the user",
                            },
                        }
                    )
                    raise Halt("MCP requested interactive authentication/approval; stop for user")
                continue
            if msg.get("id") == request_id:
                if "error" in msg:
                    # Do not log error payloads that could contain account identifiers.
                    raise Halt(
                        f"Codex protocol request failed: {method} ({msg['error'].get('code')})"
                    )
                return msg["result"]

    def read(self, name, arguments=None):
        if name not in READ_TOOLS or name not in self.tools:
            raise Halt("broker operation is outside the read-only capability")
        if self.tools[name].get("annotations", {}).get("readOnlyHint") is not True:
            raise Halt("broker tool does not advertise read-only behavior")
        arguments = arguments or {}
        schema = self.tools[name].get("inputSchema", {})
        if set(arguments) - set(schema.get("properties", {})):
            raise Halt(f"unknown arguments for {name}")
        if set(schema.get("required", [])) - set(arguments):
            raise Halt(f"missing required arguments for {name}")
        if list(Draft202012Validator(schema).iter_errors(arguments)):
            raise Halt(f"invalid arguments for {name}")
        self.calls.append(name)
        result = self.rpc(
            "mcpServer/tool/call",
            {
                "threadId": self.thread_id,
                "server": SERVER,
                "tool": name,
                "arguments": arguments,
            },
        )
        parsed = _parse_result(
            SimpleNamespace(
                isError=result.get("isError", False),
                structuredContent=result.get("structuredContent"),
                content=[SimpleNamespace(**item) for item in result.get("content", [])],
            )
        )
        if not isinstance(parsed, dict):
            raise Halt(f"non-object broker response for {name}")
        if list(Draft202012Validator(self.tools[name].get("outputSchema", {})).iter_errors(parsed)):
            raise Halt(f"official response schema mismatch for {name}")
        return parsed

    def review_equity_once(self, arguments):
        """Explicit opt-in, one non-placing whole-share CANARY review; never placement.

        Official docs and pinned metadata describe this named tool as a simulation.
        It lacks readOnlyHint, so this exception stays outside the normal read RPC
        allowlist. Consume before sending; a timeout/error never allows a retry.
        """
        from .model import dec
        from .schema import Contracts

        if not self.allow_equity_review or self.equity_review_consumed:
            raise Halt("one-time equity review not authorized or already consumed")
        name = "review_equity_order"
        contracts = Contracts()
        status = next(s for s in self.inventory["data"] if s["name"] == SERVER)
        contracts.check_current(self.tools, status["serverInfo"]["version"])
        contracts.validate(name, arguments)
        if (
            set(arguments)
            != {
                "account_number",
                "symbol",
                "side",
                "quantity",
                "type",
                "limit_price",
                "time_in_force",
                "market_hours",
            }
            or arguments["side"] != "buy"
            or dec(arguments["quantity"]) != 1
            or arguments["type"] != "limit"
            or arguments["time_in_force"] != "gfd"
            or arguments["market_hours"] != "regular_hours"
            or not 0 < dec(arguments["limit_price"]) <= 3
        ):
            raise Halt("review is restricted to one tiny regular-hours GFD limit BUY")
        self.equity_review_consumed = True
        self.calls.append(name)
        result = self._exchange(
            "mcpServer/tool/call",
            {"threadId": self.thread_id, "server": SERVER, "tool": name, "arguments": arguments},
        )
        parsed = _parse_result(
            SimpleNamespace(
                isError=result.get("isError", False),
                structuredContent=result.get("structuredContent"),
                content=[SimpleNamespace(**item) for item in result.get("content", [])],
            )
        )
        return contracts.validate(name, parsed, "outputSchema")

    def execution_call(self, name, arguments):
        from .release import require_real_release

        require_real_release()  # Independent of Config, CLI and caller-supplied tokens.
        from .schema import Contracts

        contracts = Contracts()
        contracts.check_current(self.tools, self.inventory["data"][0]["serverInfo"]["version"])
        contracts.validate(name, arguments)
        self.calls.append(name)
        result = self._exchange(
            "mcpServer/tool/call",
            {"threadId": self.thread_id, "server": SERVER, "tool": name, "arguments": arguments},
        )
        parsed = _parse_result(
            SimpleNamespace(
                isError=result.get("isError", False),
                structuredContent=result.get("structuredContent"),
                content=[SimpleNamespace(**item) for item in result.get("content", [])],
            )
        )
        return contracts.validate(name, parsed, "outputSchema")
