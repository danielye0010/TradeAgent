"""Review-only RSI/CANARY preparation. No submission lifecycle or signing capability."""

import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .accounting import Accounting
from .broker import data, rows, unique, utc_time
from .calendar import regular_session
from .codex_bridge import ENDPOINT, READ_TOOLS, SERVER, CodexBridge
from .legacy.canary import CanarySelector
from .legacy.policy import deployment_hash
from .model import Halt, dec, digest, timestamp_fresh
from .research.domain import Bar, MarketSnapshot
from .research.lab import scan
from .risk import check_order, check_state
from .schema import Contracts, structural
from .standalone_mcp import StandaloneMCP


class CatalogOnly(StandaloneMCP):
    def rpc(self, method, params, **kwargs):
        if method not in {"initialize", "notifications/initialized", "tools/list"}:
            raise Halt("catalog capability forbids every broker call")
        return super().rpc(method, params, **kwargs)


class CanaryReviewBridge(CodexBridge):
    """Reuse review_equity_once; independently deny all execution before network.

    Complete metadata comes from metadata-only direct MCP. Codex enables only its
    existing READ_TOOLS plus review, never placement/cancellation metadata tools.
    """

    def __init__(self, root, tokens, timeout=60):
        super().__init__(root, timeout, allow_equity_review=True)
        self.tokens = tokens
        self.wire_calls = []
        self.market_evidence = {}
        self.review_arguments_hash = None
        self.review_attempted = False
        self.before_review = None
        self.review_send_hash = None

    def __enter__(self):
        with CatalogOnly(self.tokens, self.timeout) as catalog:
            complete, server = catalog.tools, catalog.server_info
        try:
            super().__enter__()
            status = next(s for s in self.inventory["data"] if s["name"] == SERVER)
            identity = ("name", "version")
            if server.get("name") != SERVER or {k: server.get(k) for k in identity} != {
                k: status.get("serverInfo", {}).get(k) for k in identity
            }:
                raise Halt("review/catalog server identities disagree")
            Contracts().check_current(complete, server.get("version"))
            for name, tool in self.tools.items():
                expected = complete.get(name)
                fields = ("inputSchema", "outputSchema", "annotations")
                if not expected or structural(
                    {k: tool[k] for k in fields if k in expected and k in tool}
                ) != (structural({k: expected[k] for k in fields if k in expected})):
                    raise Halt("review/read catalog contracts disagree")
            self.tools = complete
            self.inventory = {"data": [{"name": SERVER, "serverInfo": server}]}
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def read(self, name, arguments=None):
        response = super().read(name, arguments)
        if name in {"get_equity_quotes", "get_equity_price_book"}:
            self.market_evidence[name] = response
        return response

    def execution_call(self, *args, **kwargs):
        raise Halt("canary-review has no execution capability")

    def review_equity_once(self, arguments):
        if self.review_arguments_hash != digest(arguments):
            raise Halt("review payload differs from the prepared candidate")
        return super().review_equity_once(arguments)

    def _exchange(self, method, params):
        if method == "mcpServer/tool/call":
            name = params.get("tool")
            if params.get("server") != SERVER:
                raise Halt("review-only broker origin mismatch")
            if name == "review_equity_order":
                if (
                    not self.equity_review_consumed
                    or self.review_attempted
                    or self.review_arguments_hash != digest(params.get("arguments"))
                    or self.before_review is None
                ):
                    raise Halt("one-time review boundary missing or consumed")
                self.before_review()
                self.review_attempted = True  # Before network; never reset or retry.
            elif name not in READ_TOOLS or (
                self.tools.get(name, {}).get("annotations", {}).get("readOnlyHint") is not True
            ):
                raise Halt("canary-review blocks every broker execution/write")
            if name == "review_equity_order":
                self.review_send_hash = self.review_arguments_hash
        elif method not in {"initialize", "thread/start", "mcpServerStatus/list"}:
            raise Halt("unsupported review-only protocol method")
        try:
            return super()._exchange(method, params)
        finally:
            self.review_send_hash = None

    def send(self, message):
        method = message.get("method")
        if method == "mcpServer/tool/call":
            params = message.get("params", {})
            name = params.get("tool")
            if params.get("server") != SERVER:
                raise Halt("review-only send origin mismatch")
            if name == "review_equity_order":
                if self.review_send_hash != digest(params.get("arguments")):
                    raise Halt("review send lacks one-use authorization")
                self.review_send_hash = None  # Consume before actual pipe/network I/O.
            elif (
                name not in READ_TOOLS
                or self.tools.get(name, {}).get("annotations", {}).get("readOnlyHint") is not True
            ):
                raise Halt("review-only send rejects all execution/write tools")
            self.wire_calls.append(name)
        elif method not in {"initialize", "initialized", "thread/start", "mcpServerStatus/list"}:
            if set(message) != {"id", "error"}:
                raise Halt("review-only send rejects unknown protocol methods")
        return super().send(message)


def live_rsi_snapshots(bridge, symbols, clock=time.time, recorder=lambda value: None):
    """Conservative receipt availability; never backdate quotes or completed bars."""
    benchmark = "SPY"
    if benchmark in symbols:
        raise Halt("RSI symbol must differ from its frozen SPY benchmark")
    universe = sorted(set(symbols) | {benchmark})
    started = clock()
    quote_data = data(bridge.read("get_equity_quotes", {"symbols": universe}))
    quote_receipt = clock()
    quotes = unique([r["quote"] for r in rows(quote_data.get("results"), "quotes")], "symbol")
    history_data = data(
        bridge.read(
            "get_equity_historicals",
            {
                "symbols": universe,
                "start_time": (
                    datetime.fromtimestamp(started, timezone.utc) - timedelta(days=3)
                ).isoformat(),
                "end_time": datetime.fromtimestamp(started, timezone.utc).isoformat(),
                "interval": "5minute",
                "bounds": "regular",
                "adjustment_type": "split",
            },
        )
    )
    received = clock()
    history = unique(rows(history_data.get("results"), "RSI bars"), "symbol")
    if set(history) != set(universe) or set(quotes) != set(universe):
        raise Halt("incomplete RSI symbol/benchmark coverage")
    bars = {}
    observation = {
        "request_start": started,
        "quote_receipt": quote_receipt,
        "history_receipt": received,
        "quotes": quotes,
        "raw_histories": history,
        "latest_completed_ends": {},
        "usable_bar_counts": {},
    }
    for symbol, result in history.items():
        if result.get("interval") != "5minute" or result.get("bounds") != "regular":
            raise Halt("unexpected RSI bar interval/session")
        bars[symbol] = tuple(
            Bar(
                symbol,
                utc_time(b["begins_at"]),
                utc_time(b["begins_at"]) + 300,
                received,
                *[
                    float(dec(b[k]))
                    for k in ("open_price", "high_price", "low_price", "close_price")
                ],
                float(dec(b["volume"])),
            )
            for b in rows(result.get("bars"), "RSI history")
            if b.get("interpolated") is not True and utc_time(b["begins_at"]) + 300 <= started
        )
        observation["usable_bar_counts"][symbol] = len(bars[symbol])
        if not bars[symbol]:
            recorder(observation)
            raise Halt(f"no usable completed RSI bars for {symbol}")
        observation["latest_completed_ends"][symbol] = bars[symbol][-1].end
    recorder(observation)
    snapshots = {}
    for symbol in symbols:
        q = quotes[symbol]
        decision = bars[symbol][-1].end
        snapshots[symbol] = MarketSnapshot(
            symbol,
            decision,
            ENDPOINT,
            "prospective",
            benchmark,
            bars[symbol],
            bars[benchmark],
            float(dec(q["bid_price"])),
            float(dec(q["ask_price"])),
            max(utc_time(q[k]) for k in ("venue_bid_time", "venue_ask_time")),
            quote_receipt,
        )
    return snapshots


ORDER_GATES = (
    "state/account/accounting/freshness/valuation/drawdown",
    "regular_session",
    "asset/symbol",
    "side/quantity/whole_share/price",
    "tradability",
    "book_freshness/depth",
    "bid/ask_freshness/identity",
    "spread",
    "price_tolerance/cents",
    "daily_loss",
    "daily_turnover",
    "new_exposure",
    "concentration",
    "position_count",
    "unleveraged_cash",
    "cash_reserve",
)


def validate_review(response, intent, snapshot, risk, now):
    Contracts().validate("review_equity_order", response, "outputSchema")
    body = data(response)
    for key in ("symbol", "side", "quantity", "type", "limit_price"):
        if body.get(key) != intent.payload()[key]:
            raise Halt("review differs from exact proposed CANARY order")
    if not isinstance(body.get("order_checks"), dict) or body["order_checks"]:
        raise Halt("review warnings/checks require human investigation")
    quote = body.get("quote_data")
    if not isinstance(quote, dict) or (
        quote.get("symbol") != intent.symbol
        or quote.get("state") != "active"
        or quote.get("has_traded") is not True
    ):
        raise Halt("review quote missing/ineligible")
    if any(
        not timestamp_fresh(utc_time(quote[k]), now, risk.max_data_age_seconds)
        for k in ("venue_bid_time", "venue_ask_time", "venue_last_trade_time")
    ):
        raise Halt("stale/future review market data")
    if dec(quote["bid_price"]) != snapshot.bids[intent.symbol] or (
        dec(quote["ask_price"]) != snapshot.asks[intent.symbol]
    ):
        raise Halt("review/current quote disagreement")


def run_review(
    bridge,
    broker,
    state,
    store,
    config,
    risk,
    root,
    account_digest,
    snapshots_reader,
    facts_reader,
    clock=time.time,
):
    """Normal RSI scan/rank, unchanged CANARY selector, one review, immediate stop."""
    result = {
        "status": "HALT",
        "candidate": None,
        "proposed_order": None,
        "review": None,
        "gates": {},
        "risk_gates": dict.fromkeys(ORDER_GATES, "NOT_RUN"),
    }
    stage, run = "manifest", None
    with state.lock(config.lease_seconds) as fence, store.lock():
        run = state.start(config, risk)
        try:
            deployment_hash(root)
            result["gates"][stage] = "PASS"
            stage = "session"
            if not regular_session(clock()):
                raise Halt("XNYS regular session is closed")
            result["gates"][stage] = "PASS"
            stage = "account/market/startup_reconciliation"
            snapshot = broker.snapshot()
            if digest(broker.account["account_number"]) != account_digest:
                raise Halt("exact dedicated account mismatch")
            result["market"] = {
                "bid": snapshot.bids,
                "ask": snapshot.asks,
                "depth": snapshot.liquidity,
                "quote_times": snapshot.quote_times,
            }
            result["account"] = {
                "cash": snapshot.cash,
                "nav": snapshot.nav,
                "buying_power": snapshot.buying_power,
                "positions": snapshot.positions,
                "orders": snapshot.orders,
            }
            check_state(snapshot, risk, clock())
            state.recover(run, snapshot)
            day = datetime.fromtimestamp(clock(), ZoneInfo("America/New_York")).date().isoformat()
            baseline = Accounting(state).observe(run, snapshot, day)
            result["gates"][stage] = "PASS"
            stage = "RSI_snapshot/strategy/ranking"
            snapshots = snapshots_reader()
            if set(snapshots) != set(config.allowed_symbols):
                raise Halt("RSI snapshots must cover the exact frozen universe")
            evidence = []
            positive = set()
            for symbol in sorted(snapshots):
                market = snapshots[symbol]
                if market.symbol != symbol or market.evidence_kind != "prospective":
                    raise Halt("RSI review requires exact prospective symbol identity")
                if (
                    dec(market.bid) != snapshot.bids[symbol]
                    or dec(market.ask) != snapshot.asks[symbol]
                ):
                    raise Halt("RSI/current quote disagreement")
                scanned = scan(store, market, clock())
                if scanned["status"] != "persisted":
                    raise Halt("RSI snapshot already used; no repeated review")
                predictions = [
                    dict(r)
                    for r in store.db.execute(
                        "SELECT * FROM predictions WHERE snapshot_id=? ORDER BY prediction_id",
                        (scanned["snapshot_id"],),
                    )
                ]
                evidence.append({"symbol": symbol, **scanned, "prediction_evidence": predictions})
                if any(p["kind"] == "UNDERLYING" for p in scanned["trade_plans"]):
                    positive.add(symbol)
            result["RSI"] = evidence
            state.event(run, "RSI_review_evidence", evidence)
            result["gates"][stage] = "PASS"
            if not positive:
                result.update(
                    status="NO_TRADE", reason="frozen RSI has no selected long underlying"
                )
                return result
            stage = "classification/history/CANARY_selection"
            facts, histories = facts_reader(), broker.histories()
            if set(facts) != set(config.allowed_symbols):
                raise Halt("CANARY classification incomplete")
            selector = CanarySelector()
            selected, decisions = selector.select(
                snapshot, config, risk, facts, histories, clock(), baseline
            )
            result["selection"] = decisions
            result["gates"][stage] = "PASS"
            if selected is None or selected.symbol not in positive:
                result.update(
                    status="NO_TRADE", reason="unchanged CANARY selection and RSI do not agree"
                )
                return result
            stage = "all_order_risk"
            check_order(selected, snapshot, config, risk, clock(), baseline)
            result["risk_gates"] = dict.fromkeys(ORDER_GATES, "PASS")
            result["gates"][stage] = "PASS"
            result.update(
                candidate=selected.symbol,
                proposed_order=selected.payload(),
                max_notional=str(selected.quantity * selected.limit_price),
                expected_cash_after=str(snapshot.cash - selected.quantity * selected.limit_price),
                risk_limits=asdict(risk),
                canary_limits=asdict(selector.rules),
            )

            def before_review():
                fence()
                deployment_hash(root)
                if not regular_session(clock()):
                    raise Halt("regular session ended before review")
                check_order(selected, snapshot, config, risk, clock(), baseline)
                facts[selected.symbol].validate(clock(), canary=True)

            bridge.before_review = before_review
            stage = "review"
            started = clock()
            arguments = {"account_number": broker.account["account_number"], **selected.payload()}
            bridge.review_arguments_hash = digest(arguments)
            response = bridge.review_equity_once(arguments)
            # Scrub the private account even if provider guide text mentions it.
            result["review"] = json.loads(
                json.dumps(response).replace(broker.account["account_number"], "[REDACTED_ACCOUNT]")
            )
            validate_review(response, selected, snapshot, risk, clock())
            if clock() >= started + 30:
                raise Halt("review expired; no re-review")
            result["gates"][stage] = "PASS"
            result.update(
                status="READY_FOR_USER_CONFIRMED_MANUAL_CANARY",
                review_expires=started + 30,
                meaning="manual Robinhood action only; no execution authorization",
            )
            return result  # No broker reads, placement lifecycle or retry after review.
        except (Halt, ValueError, OSError, KeyError, TypeError) as exc:
            result["gates"][stage] = "FAIL"
            result["blocker"] = (
                str(exc) if isinstance(exc, (Halt, ValueError)) else type(exc).__name__
            )
            return result
        finally:
            result["market_read_evidence"] = bridge.market_evidence
            result["broker_counts"] = {
                "reads": sum(n in READ_TOOLS for n in bridge.wire_calls),
                "review": bridge.wire_calls.count("review_equity_order"),
                "place": bridge.wire_calls.count("place_equity_order"),
                "cancel": bridge.wire_calls.count("cancel_equity_order"),
            }
            sanitized = json.loads(
                json.dumps(result, default=str).replace(
                    str((broker.account or {}).get("account_number", "[NO_ACCOUNT]")),
                    "[REDACTED_ACCOUNT]",
                )
            )
            result.clear()
            result.update(sanitized)
            state.event(run, "canary_review_result", result)
            state.finish(run, result["status"])
            state.export_log()
