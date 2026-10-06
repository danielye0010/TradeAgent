from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from test_release_policy import histories, tiny_snapshot

from robinhood_agent.canary import CanarySelector, nasdaq_common_reference
from robinhood_agent.model import Config, Halt, Risk, dec
from robinhood_agent.simulator import SimClock

NOW = datetime(2026, 10, 6, 12, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()
HEADER = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"
)


def directory(name="Tiny Inc. - Common Stock", flags="Q|N|N|100|N|N", extra=""):
    return f"{HEADER}\nTINY|{name}|{flags}\n{extra}File Creation Time: 1006202612:11|||||||\n"


def instruments(name="Tiny Inc Common Stock"):
    return [{"symbol": "TINY", "instrument_id": "synthetic-only", "name": name}]


def classify(text=None, items=None, receipt=NOW, now=NOW):
    return nasdaq_common_reference(
        directory() if text is None else text,
        instruments() if items is None else items,
        "TINY",
        receipt,
        now,
    )


def test_exchange_type_is_positive_but_directory_cannot_clear_actions():
    facts, evidence = classify()
    assert facts.ordinary_common is True
    assert facts.adr is facts.etf is facts.leveraged_inverse is False
    assert facts.exchange == "XNAS"
    assert facts.corporate_action_clear is None
    assert evidence["classification"]["preferred"] is False
    assert evidence["classification"]["warrant_right_unit"] is False
    assert evidence["reason_codes"] == ["CORPORATE_ACTION_COVERAGE_UNVERIFIED"]
    assert len(evidence["directory_sha256"]) == len(facts.evidence_hash) == 64
    with pytest.raises(Halt, match="classification unverified"):
        facts.validate(NOW)


@pytest.mark.parametrize(
    "name",
    [
        "Tiny Inc. - American Depositary Shares",
        "Tiny Inc. ADR - Common Stock",
        "Tiny Inc. ADS - Common Stock",
        "Tiny Inc. - Preferred Stock",
        "Tiny Inc. - Warrant to buy Common Stock",
        "Tiny Inc. - Rights",
        "Tiny Inc. - Units",
        "Tiny Inc. ETF - Common Stock",
        "Tiny Fund - Common Stock",
        "Tiny Leveraged - Common Stock",
        "Tiny Inverse - Common Stock",
        "Tiny 2X - Common Stock",
        "Tiny Inc. - Ordinary Shares",
        "Tiny Inc. Common Stock",
    ],
)
def test_non_explicit_or_excluded_issue_types_are_never_classified(name):
    facts, evidence = classify(directory(name))
    assert facts is None
    assert evidence["reason_codes"][0] in {
        "EXCLUDED_SECURITY_TYPE",
        "EXPLICIT_COMMON_ISSUE_TYPE_UNVERIFIED",
    }


@pytest.mark.parametrize(
    "flags",
    ["?|N|N|100|N|N", "Q|Y|N|100|N|N", "Q|N|D|100|N|N", "Q|N|N|100|Y|N", "Q|N|N|100|N|Y"],
)
def test_unknown_test_deficient_fund_flags_fail_closed(flags):
    facts, evidence = classify(directory(flags=flags))
    assert facts is None
    assert evidence["reason_codes"] == ["EXCLUDED_OR_UNKNOWN_EXCHANGE_FLAGS"]


@pytest.mark.parametrize("items", [[], instruments() * 2, [{"symbol": "TINY"}]])
def test_missing_ambiguous_mcp_identity_is_not_invented(items):
    facts, evidence = classify(items=items)
    assert facts is None
    assert evidence["reason_codes"] == ["MCP_IDENTITY_MISSING_OR_AMBIGUOUS"]


def test_common_stock_class_mismatch_is_rejected():
    facts, evidence = classify(items=instruments("Tiny Inc Class A Common Stock"))
    assert facts is None
    assert evidence["reason_codes"] == ["MCP_EXCHANGE_NAME_MISMATCH"]


@pytest.mark.parametrize(
    "text,code",
    [
        (directory().replace("|NextShares", "|NewSchema"), "REFERENCE_SCHEMA_UNVERIFIED"),
        (directory().replace("1006202612:11", "broken"), "REFERENCE_CREATION_TIME_UNVERIFIED"),
        (
            directory().replace("1006202612:11", "1306202612:11"),
            "REFERENCE_CREATION_TIME_UNVERIFIED",
        ),
        (directory().replace("1006202612:11", "1005202612:11"), "REFERENCE_NOT_CURRENT"),
        (directory().replace("1006202612:11", "1006202613:11"), "REFERENCE_NOT_CURRENT"),
        (directory(extra="TINY|Duplicate|Q|N|N|100|N|N\n"), "REFERENCE_MALFORMED_OR_DUPLICATE"),
        (directory().replace("|100|", "|100|extra|"), "REFERENCE_MALFORMED_OR_DUPLICATE"),
        (directory().replace("TINY|", "OTHER|"), "EXCHANGE_IDENTITY_UNVERIFIED"),
    ],
)
def test_reference_freshness_schema_and_identity_fail_closed(text, code):
    facts, evidence = classify(text)
    assert facts is None
    assert evidence["reason_codes"] == [code]


@pytest.mark.parametrize("receipt", [NOW - 86401, NOW + 0.001])
def test_bad_receipt_timestamp_fail_closed(receipt):
    facts, evidence = classify(receipt=receipt)
    assert facts is None
    assert evidence["reason_codes"] == ["REFERENCE_RECEIPT_STALE_OR_FUTURE"]


def test_evidence_fingerprint_changes_with_broker_identity():
    facts, _ = classify()
    other, _ = classify(items=[{**instruments()[0], "instrument_id": "changed"}])
    assert facts.evidence_hash != other.evidence_hash


def test_selector_rejects_unknown_actions_with_zero_execution_capability():
    facts, _ = classify()
    clock = SimClock(NOW)
    selected, decisions = CanarySelector().select(
        tiny_snapshot(clock),
        Config(allowed_symbols=["TINY"]),
        Risk(),
        {"TINY": facts},
        histories(clock),
        NOW,
        dec(100),
    )
    assert selected is None
    assert decisions == [
        {
            "symbol": "TINY",
            "decision": "reject",
            "reason": "ordinary US common/non-ADR/non-ETF/corporate-action classification unverified",
        }
    ]
