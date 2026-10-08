"""Direct official MCP Streamable HTTP. No Codex, model or credential store.

The operator supplies an external noninteractive OAuth helper. This module never
performs login, persists credentials, follows redirects or retries a write.
Only classified transient idempotent reads have bounded retries.
"""

import http.client
import json
import os
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from .model import Halt
from .schema import Contracts
from .vendor.client import _parse_result

ENDPOINT = "https://agent.robinhood.com/mcp/trading"
HOST, PATH = "agent.robinhood.com", "/mcp/trading"
PROTOCOLS = {"2025-03-26", "2025-06-18", "2025-11-25"}
READ_TOOLS = frozenset(
    {
        "get_trade_approval_setting",
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_equity_quotes",
        "get_equity_historicals",
        "get_equity_tradability",
        "get_equity_price_book",
        "search",
    }
)
MAX_RESPONSE = 32 * 1024 * 1024


class TransientReadFailure(Halt):
    """Only idempotent reads may retry this classified transport failure."""


class ExternalOAuthToken:
    """Credential helper owns OAuth refresh outside this repository; no shell."""

    def __init__(self, executable, root, clock=time.time):
        self.path = Path(executable).resolve()
        self.root, self.clock = Path(root).resolve(), clock
        if not self.path.is_absolute() or self.path.is_relative_to(self.root):
            raise Halt("OAuth helper must live outside the repository")
        info = self.path.stat()
        if (
            os.name != "posix"
            or str(self.path).startswith("/mnt/")
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not os.access(self.path, os.X_OK)
        ):
            raise Halt("OAuth helper requires owner-only local executable permissions")

    def __call__(self):
        try:
            result = subprocess.run(
                [str(self.path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode or len(result.stdout) > 16384:
                raise Halt("external OAuth helper unavailable; personal authentication required")
            token = json.loads(result.stdout)
            if (
                set(token) != {"access_token", "expires_at", "resource"}
                or token["resource"] != ENDPOINT
                or type(token["expires_at"]) not in (int, float)
                or not self.clock() + 30 < token["expires_at"] < self.clock() + 86400
                or not isinstance(token["access_token"], str)
                or not token["access_token"]
                or len(token["access_token"]) > 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in token["access_token"])
            ):
                raise Halt("OAuth helper returned invalid resource-bound token")
            return token["access_token"]
        except Halt:
            raise
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
            raise Halt("OAuth helper failed; credentials are never logged") from exc


class StandaloneMCP:
    is_simulation = False

    def __init__(self, token_source, timeout=60):
        self.token_source, self.timeout = token_source, timeout
        self.serial, self.session, self.protocol = 0, None, None
        self.tools, self.calls = {}, []
        self.contracts = Contracts()

    def __enter__(self):
        result = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "tradeagent", "version": "0.1.0"},
            },
        )
        self.protocol = result.get("protocolVersion")
        if self.protocol not in PROTOCOLS:
            raise Halt("unsupported negotiated MCP protocol; review required")
        self.server_info = result.get("serverInfo", {})
        self.rpc("notifications/initialized", {}, notification=True)
        cursor, seen = None, set()
        for _ in range(100):
            result = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            for tool in result.get("tools", []):
                name = tool.get("name")
                if not isinstance(name, str) or name in self.tools:
                    raise Halt("invalid/duplicate official tool identity")
                self.tools[name] = tool
            cursor = result.get("nextCursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise Halt("official catalog cursor invalid")
            seen.add(cursor)
        else:
            raise Halt("official catalog pagination limit")
        self.contracts.check_current(self.tools, self.server_info.get("version"))
        self.auth_status = "external OAuth token; authenticated catalog validated"
        return self

    def __exit__(self, *_):
        # No automatic DELETE, cancellation, reconnect, retry or credential write.
        self.session = None

    def rpc(self, method, params, *, notification=False, before_send=None):
        if method not in {"initialize", "notifications/initialized", "tools/list", "tools/call"}:
            raise Halt("unsupported standalone protocol method")
        if method == "tools/call" and params.get("name") not in READ_TOOLS:
            if type(before_send) is not WireAuthorization:
                raise Halt("execution requires the single owner lifecycle boundary")
        self.serial += 1
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            message["id"] = self.serial
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self.token_source(),
        }
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        conn = http.client.HTTPSConnection(HOST, timeout=self.timeout)
        try:
            if before_send is not None:
                if type(before_send) is not WireAuthorization:
                    raise Halt("untrusted broker send authorization")
                before_send.validate_request(params)
                before_send()  # AFTER token refresh, immediately before the only network send.
            if method == "tools/call":
                self.calls.append(params["name"])
            conn.request(
                "POST", PATH, body=json.dumps(message, allow_nan=False).encode(), headers=headers
            )
            response = conn.getresponse()
            if response.status == 401:
                raise Halt("official OAuth expired; personal authentication required; no retry")
            if response.status not in ({202} if notification else {200}):
                if (
                    method == "tools/call"
                    and params.get("name") in READ_TOOLS
                    and response.status in {429, 502, 503, 504}
                ):
                    raise TransientReadFailure(f"idempotent read HTTP {response.status}")
                raise Halt("official MCP HTTP failure; no retry or redirect")
            if notification:
                return None
            session = response.getheader("Mcp-Session-Id")
            if session:
                if (
                    len(session) > 8192
                    or any(not 33 <= ord(c) <= 126 for c in session)
                    or (self.session and self.session != session)
                ):
                    raise Halt("invalid/changed MCP session")
                self.session = session
            content_type = response.getheader("Content-Type", "").split(";")[0]
            if content_type == "application/json":
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise Halt("oversized MCP response")
                messages = [json.loads(raw)]
            elif content_type == "text/event-stream":
                messages = self._sse(response)
            else:
                raise Halt("unsupported official response content type")
            for reply in messages:
                if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
                    raise Halt("malformed MCP envelope")
                if "method" in reply:
                    if "id" in reply:
                        raise Halt("MCP requested interactive approval/auth; operator required")
                    continue
                if reply.get("id") != message["id"]:
                    raise Halt("MCP reply identity mismatch")
                if "error" in reply or "result" not in reply:
                    raise Halt("official MCP error; no retry")
                return reply["result"]
            raise Halt("missing MCP acknowledgment; no automatic replay")
        except Halt as exc:
            if (
                method == "tools/call"
                and params.get("name") in READ_TOOLS
                and str(exc)
                in {"MCP stream disconnected; no replay", "MCP stream deadline; no replay"}
            ):
                raise TransientReadFailure("idempotent read stream interrupted") from exc
            raise
        except (OSError, http.client.HTTPException) as exc:
            if method == "tools/call" and params.get("name") in READ_TOOLS:
                raise TransientReadFailure("idempotent read network failure") from exc
            raise Halt("MCP network/response failure; outcome unresolved; no replay") from exc
        except ValueError as exc:
            raise Halt("MCP malformed response; no replay") from exc
        finally:
            conn.close()

    def _sse(self, response):
        size, fields = 0, []
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            line = response.readline(MAX_RESPONSE + 1)
            size += len(line)
            if size > MAX_RESPONSE:
                raise Halt("oversized MCP SSE response")
            if not line:
                raise Halt("MCP stream disconnected; no replay")
            line = line.decode("utf-8").rstrip("\r\n")
            if not line:
                if fields:
                    reply = json.loads("\n".join(fields))
                    fields = []
                    yield reply
                    if "result" in reply or "error" in reply:
                        return
            elif line.startswith("data:"):
                fields.append(line[5:].lstrip(" "))
        raise Halt("MCP stream deadline; no replay")

    def _call(self, name, arguments, before_send=None):
        if name not in self.tools:
            raise Halt("official tool unavailable")
        for direction, value in (("inputSchema", arguments),):
            schema = self.tools[name].get(direction, {})
            if set(arguments) - set(schema.get("properties", {})) or list(
                Draft202012Validator(schema).iter_errors(value)
            ):
                raise Halt("official tool arguments invalid")
        response = self.rpc(
            "tools/call", {"name": name, "arguments": arguments}, before_send=before_send
        )
        if response.get("isError") is True:
            raise Halt("official tool returned an error; no automatic retry")
        result = _parse_result(
            SimpleNamespace(
                isError=response.get("isError", False),
                structuredContent=response.get("structuredContent"),
                content=[SimpleNamespace(**item) for item in response.get("content", [])],
            )
        )
        if not isinstance(result, dict) or list(
            Draft202012Validator(self.tools[name].get("outputSchema", {})).iter_errors(result)
        ):
            raise Halt("official tool response mismatch")
        return result

    def read(self, name, arguments=None):
        if (
            name not in READ_TOOLS
            or self.tools.get(name, {}).get("annotations", {}).get("readOnlyHint") is not True
        ):
            raise Halt("standalone read capability rejects writes")
        for attempt in range(3):
            try:
                return self._call(name, arguments or {})
            except TransientReadFailure:
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))


class ReadOnlyMCP(StandaloneMCP):
    """Validate only frozen read contracts; writes rejected before credentials/HTTP."""

    def __init__(self, token_source, timeout=20):
        from .model import digest

        super().__init__(token_source, timeout)
        self.contracts.tools = {n: v for n, v in self.contracts.tools.items() if n in READ_TOOLS}
        self.contracts.hash = digest(self.contracts.tools)

    def rpc(self, method, params, *, notification=False, before_send=None):
        if before_send is not None or (
            method == "tools/call" and params.get("name") not in READ_TOOLS
        ):
            raise Halt("read-only preflight cannot review, place, cancel or authorize writes")
        return super().rpc(method, params, notification=notification)


@dataclass(frozen=True)
class WireAuthorization:
    """A validated owner context and durable-intent proof, never a caller-supplied boolean."""

    guard: object
    state: object
    key: str
    name: str
    arguments_hash: str
    intent: object
    snapshot: object
    baseline: object

    def validate_request(self, params):
        from .model import digest

        args = params.get("arguments")
        if (
            self.name not in {"review_equity_order", "place_equity_order", "cancel_equity_order"}
            or params.get("name") != self.name
            or digest(args) != self.arguments_hash
            or not isinstance(args, dict)
        ):
            raise Halt("network payload differs from durable release authorization")
        row = self.state.db.execute("SELECT * FROM intents WHERE key=?", (self.key,)).fetchone()
        if not row or json.loads(row["payload"]) != self.intent.payload():
            raise Halt("network intent differs from durable journal")
        expected = {"account_number": args.get("account_number"), **self.intent.payload()}
        from .execution_policy import OwnerPolicy

        owner = type(self.guard) is OwnerPolicy
        account_digest = self.guard.account_digest if owner else self.guard.context.account_digest
        if digest(expected["account_number"]) != account_digest:
            raise Halt("network account differs from execution policy")
        if self.name == "cancel_equity_order":
            if not owner or row["status"] != "pending" or not row["broker_id"]:
                raise Halt("cancellation requires owner-authorized known pending intent")
            expected = {"account_number": args["account_number"], "order_id": row["broker_id"]}
            if not self.state.db.execute(
                "SELECT 1 FROM one_shot_meta WHERE key=?", ("cancel_reserved:" + self.key,)
            ).fetchone():
                raise Halt("cancellation lacks durable one-attempt reservation")
        if self.name == "place_equity_order":
            expected["ref_id"] = row["ref_id"]
            plan = self.state.db.execute(
                "SELECT packet FROM plans WHERE key=?", (self.key,)
            ).fetchone()
            consumed = self.state.db.execute(
                "SELECT consumed FROM approvals WHERE key=?", (self.key,)
            ).fetchone()
            if not plan or not consumed or consumed[0] != 1:
                raise Halt("network placement lacks consumed exact review")
            if digest(args) != json.loads(plan[0])["binding"]["submission_hash"]:
                raise Halt("network payload differs from reviewed payload")
            if (
                self.guard.validate().get("phase") == "CANARY"
                and not self.state.db.execute(
                    "SELECT 1 FROM policy_decisions WHERE grant_id=? AND side=? AND key=?",
                    (self.guard.artifact["policy"]["grant_id"], self.intent.side, self.key),
                ).fetchone()
            ):
                raise Halt("network placement lacks canary side reservation")
        if args != expected:
            raise Halt("network payload differs from validated equity intent")

    def __call__(self):
        from .execution_policy import OwnerPolicy
        from .legacy.release import require_real_release

        if type(self.guard) is OwnerPolicy:
            if self.guard.simulation is not False:
                raise Halt("simulation grant cannot reach real HTTP write boundary")
            self.guard.check(
                self.intent, self.snapshot, self.baseline, cancel=self.name == "cancel_equity_order"
            )
        else:
            require_real_release(self.guard, self.intent, self.snapshot, self.baseline)
        row = self.state.db.execute(
            "SELECT status FROM intents WHERE key=?", (self.key,)
        ).fetchone()
        expected = {
            "place_equity_order": "submitting",
            "review_equity_order": "prepared",
            "cancel_equity_order": "pending",
        }[self.name]
        if not row or row["status"] != expected:
            raise Halt("durable intent is no longer executable")
        if self.name == "place_equity_order":
            plan = self.state.db.execute(
                "SELECT packet FROM plans WHERE key=?", (self.key,)
            ).fetchone()
            if not plan or self.guard.clock() >= json.loads(plan[0])["binding"]["expires"]:
                raise Halt("review expired immediately before network send")


class StandaloneExecutionTransport:
    """Wire adapter only. Intent, review, idempotency and fills stay in existing engine."""

    is_simulation = False

    def __init__(self, bridge, state, broker, guard):
        self.bridge, self.state, self.broker, self.guard = bridge, state, broker, guard

    def invoke(self, name, arguments):
        from .execution_policy import OwnerPolicy
        from .legacy.standalone import intent_from_row
        from .model import digest
        from .supervised import state_binding

        owner = type(self.guard) is OwnerPolicy
        if name not in {"review_equity_order", "place_equity_order"} and not (
            owner and name == "cancel_equity_order"
        ):
            raise Halt("standalone policy permits equity review/place only; no cancellation")
        self.bridge.contracts.check_current(
            self.bridge.tools, self.bridge.server_info.get("version")
        )
        self.bridge.contracts.validate(name, arguments)
        rows = self.state.db.execute(
            "SELECT * FROM intents WHERE status IN ('prepared','submitting','pending')"
        )
        matches = []
        for row in rows:
            intent = intent_from_row(row)
            payload = {"account_number": self.broker.account["account_number"], **intent.payload()}
            if name == "cancel_equity_order":
                payload = {
                    "account_number": self.broker.account["account_number"],
                    "order_id": row["broker_id"],
                }
            if name == "place_equity_order":
                payload["ref_id"] = row["ref_id"]
            if arguments == payload:
                matches.append((row, intent))
        if len(matches) != 1:
            raise Halt("broker call is not an exact durable lifecycle intent")
        row, intent = matches[0]
        snapshot = self.broker.snapshot()
        day = (
            datetime.fromtimestamp(self.guard.clock(), ZoneInfo("America/New_York"))
            .date()
            .isoformat()
        )
        from .accounting import Accounting

        baseline = Accounting(self.state).observe(row["run_id"], snapshot, day)
        if name == "place_equity_order":
            plan = self.state.db.execute(
                "SELECT packet FROM plans WHERE key=?", (row["key"],)
            ).fetchone()
            approval = self.state.db.execute(
                "SELECT consumed FROM approvals WHERE key=?", (row["key"],)
            ).fetchone()
            if not plan or not approval or approval[0] != 1 or row["status"] != "submitting":
                raise Halt("placement requires committed reviewed/consumed intent")
            packet = json.loads(plan[0])
            if (
                digest(arguments) != packet["binding"]["submission_hash"]
                or state_binding(snapshot, economic_only=owner) != packet["binding"]["state_hash"]
                or self.guard.clock() >= packet["binding"]["expires"]
            ):
                raise Halt("account/market/review changed at execution choke point")
            if (
                self.guard.validate().get("phase") == "CANARY"
                and not self.state.db.execute(
                    "SELECT 1 FROM policy_decisions WHERE grant_id=? AND side=? AND key=?",
                    (self.guard.artifact["policy"]["grant_id"], intent.side, row["key"]),
                ).fetchone()
            ):
                raise Halt("one-time canary reservation missing")
        return self.bridge._call(
            name,
            arguments,
            before_send=WireAuthorization(
                self.guard,
                self.state,
                row["key"],
                name,
                digest(arguments),
                intent,
                snapshot,
                baseline,
            ),
        )
