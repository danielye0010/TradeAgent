"""Deterministic one-share infrastructure selection; no return ranking or writes."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR

from ..model import Halt, Intent, dec, digest, timestamp_fresh
from ..risk import TERMINAL, check_order


@dataclass(frozen=True)
class InstrumentFacts:
    symbol: str
    instrument_id: str
    asof: float
    exchange: str | None
    ordinary_common: bool | None
    adr: bool | None
    etf: bool | None
    leveraged_inverse: bool | None
    corporate_action_clear: bool | None
    evidence_source: str
    evidence_hash: str
    known_actions_checked_at: float | None = None
    known_actions_evidence_hash: str = ""

    def validate(self, now, *, canary=False):
        if (
            not self.instrument_id
            or not self.evidence_source
            or len(self.evidence_hash) != 64
            or not timestamp_fresh(self.asof, now, 86400)
        ):
            raise Halt("missing/stale instrument classification evidence")
        if (
            self.exchange not in {"XNYS", "XNAS", "XASE"}
            or self.ordinary_common is not True
            or any(v is not False for v in (self.adr, self.etf, self.leveraged_inverse))
            or (
                self.corporate_action_clear is not True
                and not (
                    canary is True
                    and self.corporate_action_clear is None
                    and timestamp_fresh(self.known_actions_checked_at, now, 86400)
                    and len(self.known_actions_evidence_hash) == 64
                )
            )
        ):
            raise Halt(
                "ordinary US common/non-ADR/non-ETF/corporate-action classification unverified"
            )


@dataclass(frozen=True)
class CanaryRules:
    max_notional: str = "3"
    max_nav_fraction: str = "0.03"
    min_depth_shares: int = 1000
    min_history_bars: int = 205
    max_history_age_seconds: int = 604800
    selector_version: str = "lowest-eligible-one-share-known-actions-v2"


class CanarySelector:
    def __init__(self, rules=None):
        self.rules = rules or CanaryRules()

    @property
    def hash(self):
        return digest(asdict(self.rules))

    def select(self, snapshot, config, risk, facts, histories, now, baseline):
        eligible, decisions = [], []
        if snapshot.positions or any(o.get("state") not in TERMINAL for o in snapshot.orders):
            raise Halt("canary requires no existing position/order conflict")
        if (
            self.rules.min_depth_shares < risk.min_equity_depth_shares
            or self.rules.min_history_bars < 205
            or self.rules.max_history_age_seconds > risk.max_history_age_seconds
        ):
            raise Halt("canary rules weaken existing gates")
        if not 0 < dec(self.rules.max_notional) <= dec(3) or not 0 < dec(
            self.rules.max_nav_fraction
        ) <= dec(".03"):
            raise Halt("canary notional/fraction exceeds hard cap")
        for symbol in sorted(config.allowed_symbols):
            try:
                f = facts.get(symbol)
                if not isinstance(f, InstrumentFacts) or f.symbol != symbol:
                    raise Halt("instrument classification unavailable")
                f.validate(now, canary=True)
                bars = histories.get(symbol, [])
                if len(bars) < self.rules.min_history_bars:
                    raise Halt("inadequate completed history")
                from ..broker import utc_time

                times = [utc_time(bar["begins_at"]) for bar in bars]
                if any(a >= b for a, b in zip(times, times[1:], strict=False)) or any(
                    dec(bar["close_price"]) <= 0 for bar in bars
                ):
                    raise Halt("invalid/out-of-order completed history")
                if (
                    datetime.fromtimestamp(times[-1], timezone.utc).date()
                    >= datetime.fromtimestamp(now, timezone.utc).date()
                ):
                    raise Halt("incomplete current-day history")
                if not timestamp_fresh(
                    utc_time(bars[-1]["begins_at"]), now, self.rules.max_history_age_seconds
                ):
                    raise Halt("stale history")
                price = snapshot.asks[symbol].quantize(dec("0.01"), rounding=ROUND_CEILING)
                if price > min(
                    dec(self.rules.max_notional), snapshot.nav * dec(self.rules.max_nav_fraction)
                ):
                    raise Halt("one share too large for canary")
                book = snapshot.liquidity.get(symbol, {})
                if (
                    min(dec(book.get("bid_size")), dec(book.get("ask_size")))
                    < self.rules.min_depth_shares
                ):
                    raise Halt("canary substantial depth unavailable")
                intent = Intent(symbol, "buy", dec(1), price)
                check_order(intent, snapshot, config, risk, now, baseline)
                eligible.append((price, symbol, intent))
                decisions.append({"symbol": symbol, "decision": "eligible", "notional": str(price)})
            except (Halt, KeyError) as error:
                decisions.append({"symbol": symbol, "decision": "reject", "reason": str(error)})
        eligible.sort(key=lambda row: (row[0], row[1]))
        return (eligible[0][2] if eligible else None), decisions

    def close_intent(self, symbol, snapshot, config, risk, now, baseline):
        if snapshot.positions.get(symbol) != dec(1) or snapshot.available.get(symbol) != dec(1):
            raise Halt("test position not exactly one held and available share")
        intent = Intent(
            symbol,
            "sell",
            dec(1),
            snapshot.bids[symbol].quantize(dec("0.01"), rounding=ROUND_FLOOR),
        )
        check_order(intent, snapshot, config, risk, now, baseline)
        return intent


NASDAQ_DIRECTORY_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NASDAQ_REFERENCE_VERSION = "nasdaq-explicit-common-reference-v1"


def canary_known_action_check(facts, checks, now):
    """Bind bounded available action-source observations, only for one-share CANARY.

    Empty observed entries is not comprehensive action clearance. Any reported
    action/ambiguity, incomplete check, stale source or positive action flag halts.
    Ordinary production facts.validate remains strict and rejects these facts.
    """
    required = {
        "https://www.nasdaqtrader.com/Trader.aspx?id=nasdaq-security-status-updates",
        "https://www.nasdaqtrader.com/Trader.aspx?id=nasdaq-ex-date",
    }
    if not isinstance(facts, InstrumentFacts) or facts.corporate_action_clear is False:
        raise Halt("known corporate action or instrument classification unavailable")
    if not isinstance(checks, list) or not required <= {c.get("source") for c in checks}:
        raise Halt("available corporate-action sources not checked")
    for check in checks:
        if (
            check.get("symbol") != facts.symbol
            or not timestamp_fresh(check.get("asof"), now, 86400)
            or len(check.get("sha256", "")) != 64
            or check.get("parsed") is not True
            or not isinstance(check.get("entries"), list)
        ):
            raise Halt("missing/stale available corporate-action evidence")
        if check["entries"]:
            raise Halt("known corporate-action event or ambiguity")
    evidence_hash = digest({"scope": "ONE_SHARE_CANARY_ONLY", "checks": checks})
    return replace(
        facts,
        known_actions_checked_at=min(check["asof"] for check in checks),
        known_actions_evidence_hash=evidence_hash,
        evidence_hash=digest([facts.evidence_hash, evidence_hash]),
    )


def nasdaq_common_reference(directory, instruments, symbol, received_at, now):
    """Parse exchange issue metadata; public directory never clears corporate actions.

    Pure, bounded reference fallback for official MCP instrument discovery. No
    network or broker capability. Accept only an explicit common-stock issue type
    agreed by exact symbol and normalized full name. Unknowns remain closed.
    The caller must fetch the pinned Nasdaq URL and preserve its original bytes.
    """
    import hashlib
    import re
    from zoneinfo import ZoneInfo

    evidence = {
        "symbol": symbol,
        "method": NASDAQ_REFERENCE_VERSION,
        "source": NASDAQ_DIRECTORY_URL,
        "directory_sha256": hashlib.sha256(directory.encode()).hexdigest(),
        "received_at": received_at,
        "reason_codes": [],
        "corporate_action_clear": None,
    }

    def reject(code):
        evidence["reason_codes"].append(code)
        return None, evidence

    if not timestamp_fresh(received_at, now, 86400):
        return reject("REFERENCE_RECEIPT_STALE_OR_FUTURE")
    lines = directory.splitlines()
    header = (
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|"
        "Round Lot Size|ETF|NextShares"
    )
    if len(lines) < 3 or lines[0] != header:
        return reject("REFERENCE_SCHEMA_UNVERIFIED")
    footer = re.fullmatch(r"File Creation Time: (\d{8}\d{2}:\d{2})\|{7}", lines[-1])
    if footer is None:
        return reject("REFERENCE_CREATION_TIME_UNVERIFIED")
    try:
        created = datetime.strptime(footer[1], "%m%d%Y%H:%M").replace(
            tzinfo=ZoneInfo("America/New_York")
        )
        receipt = datetime.fromtimestamp(received_at, ZoneInfo("America/New_York"))
    except (ValueError, OverflowError):
        return reject("REFERENCE_CREATION_TIME_UNVERIFIED")
    evidence["created_at"] = created.timestamp()
    if created.date() != receipt.date() or not timestamp_fresh(
        created.timestamp(), received_at, 86400
    ):
        return reject("REFERENCE_NOT_CURRENT")
    rows = [line.split("|") for line in lines[1:-1]]
    if any(len(row) != 8 for row in rows) or len({row[0] for row in rows}) != len(rows):
        return reject("REFERENCE_MALFORMED_OR_DUPLICATE")
    found = [row for row in rows if row[0] == symbol]
    if len(found) != 1:
        return reject("EXCHANGE_IDENTITY_UNVERIFIED")
    row = dict(zip(header.split("|"), found[0], strict=True))
    evidence["exchange_record"] = row
    if (
        row["Market Category"] not in {"Q", "G", "S"}
        or row["Test Issue"] != "N"
        or row["Financial Status"] != "N"
        or row["ETF"] != "N"
        or row["NextShares"] != "N"
    ):
        return reject("EXCLUDED_OR_UNKNOWN_EXCHANGE_FLAGS")
    name = row["Security Name"]
    excluded = (
        r"\b(adr|ads|depositary|depository|preferred|warrants?|rights?|units?|"
        r"etf|fund|nextshares|leveraged|inverse)\b|\b\d+[xX]\b"
    )
    if re.search(excluded, name, re.IGNORECASE):
        return reject("EXCLUDED_SECURITY_TYPE")
    if re.fullmatch(r".+?\s+-\s+(?:Class [A-Z] )?Common Stock", name) is None:
        return reject("EXPLICIT_COMMON_ISSUE_TYPE_UNVERIFIED")
    matches = [item for item in instruments if item.get("symbol") == symbol]
    if len(matches) != 1 or not matches[0].get("instrument_id"):
        return reject("MCP_IDENTITY_MISSING_OR_AMBIGUOUS")
    instrument = matches[0]

    def words(value):
        return re.findall(r"[a-z0-9]+", value.lower())

    if words(instrument.get("name", "")) != words(name):
        return reject("MCP_EXCHANGE_NAME_MISMATCH")
    evidence["mcp_instrument"] = instrument
    evidence["classification"] = {
        "exchange": "XNAS",
        "ordinary_common": True,
        "adr": False,
        "etf": False,
        "preferred": False,
        "warrant_right_unit": False,
        "leveraged_inverse": False,
    }
    evidence["reason_codes"] = ["CORPORATE_ACTION_COVERAGE_UNVERIFIED"]
    facts = InstrumentFacts(
        symbol,
        instrument["instrument_id"],
        created.timestamp(),
        "XNAS",
        True,
        False,
        False,
        False,
        None,
        NASDAQ_DIRECTORY_URL,
        digest(evidence),
    )
    return facts, evidence
