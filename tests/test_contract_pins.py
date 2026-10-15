"""Reviewed contracts: compatible broker changes keep working, breaking ones fail closed."""

import copy
import json
from importlib.resources import files

import pytest

from tradeagent.model import Halt
from tradeagent.schema import Contracts, describe, is_option_tool


def test_reviewed_170_preserves_inputs_and_adds_authenticated_approval_outputs():
    contracts = Contracts()
    contracts.manifest = json.loads(
        files("tradeagent").joinpath("contracts/official-1.7.0.json").read_text()
    )
    contracts.tools = contracts.manifest["tools"]
    old = json.loads(files("tradeagent").joinpath("contracts/official-1.6.2.json").read_text())
    assert contracts.manifest["server_version"] == "1.7.0"
    current = copy.deepcopy(contracts.tools)
    assert "get_trade_approval_setting" in current
    del current["get_trade_approval_setting"]
    for name in ("review_equity_order", "review_option_order"):
        fields = current[name]["outputSchema"]["properties"]["data"]["properties"]
        assert fields.pop("customer_approval_required") == {"type": ["null", "boolean"]}
        assert fields.pop("customer_approval_required_reason") == {"type": "string"}
    for name in ("place_equity_order", "place_option_order"):
        current[name]["outputSchema"]["properties"]["data"]["properties"].pop("approval")
    assert current == old["tools"]
    contracts.check_current(contracts.tools, "1.7.0")


CORE = [name for name in Contracts().tools if not is_option_tool(name)]
OPTIONS = [name for name in Contracts().tools if is_option_tool(name)]


def add_required_input(tool):
    schema = tool["inputSchema"]
    schema.setdefault("properties", {})["zz_new_required"] = {"type": "string"}
    schema["required"] = [*schema.get("required", []), "zz_new_required"]


def drop_output_field(tool):
    properties = tool["outputSchema"]["properties"]
    del properties[sorted(properties)[0]]


def retype_input_field(tool):
    properties = tool["inputSchema"]["properties"]
    properties[sorted(properties)[0]] = {"type": "object"}


BREAKING = {
    "new required input": add_required_input,
    "removed output field": drop_output_field,
    "retyped input field": retype_input_field,
}


@pytest.mark.parametrize("version", ["1.6.2", "1.7.0", "1.7.1", "2.0.0", None])
def test_version_bump_with_identical_schemas_is_reported_not_blocked(version):
    contracts = Contracts()
    report = contracts.check_current(contracts.tools, version)
    assert report["server_version"] == version
    assert report["broken"] == {} and report["compatible"] == {}
    assert "1.7.3" in describe(report)


@pytest.mark.parametrize("version", ["1.7.1", "2.0.0", None])
def test_strict_legacy_mode_keeps_the_exact_version_pin(version):
    contracts = Contracts()
    with pytest.raises(Halt, match="server version drift"):
        contracts.check_current(contracts.tools, version, strict=True)


@pytest.mark.parametrize("name", CORE)
@pytest.mark.parametrize("change", sorted(BREAKING))
def test_breaking_change_to_a_core_tool_fails_closed(name, change):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    if change != "new required input" and not changed[name].get(
        "outputSchema" if "output" in change else "inputSchema", {}
    ).get("properties"):
        pytest.skip("tool has no fields of this kind")
    BREAKING[change](changed[name])
    with pytest.raises(Halt, match=f"official schema drift.*{name}"):
        contracts.check_current(changed, "1.8.0")


@pytest.mark.parametrize("name", OPTIONS)
def test_breaking_option_tool_disables_only_that_tool(name):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    add_required_input(changed[name])
    report = contracts.check_current(changed, "1.8.0")
    assert list(report["broken"]) == [name]
    with pytest.raises(Halt, match=name):
        contracts.validate(name, {})
    assert contracts.validate("get_accounts", {}) == {}


@pytest.mark.parametrize("name", list(Contracts().tools))
def test_additive_changes_stay_compatible(name):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    tool = changed[name]
    tool["inputSchema"].setdefault("properties", {})["zz_new_optional"] = {"type": "string"}
    tool["inputSchema"]["additionalProperties"] = True
    output = tool["outputSchema"]
    output.setdefault("properties", {})["zz_new_output"] = {"type": "integer"}
    output["required"] = [*output.get("required", []), "zz_new_output"]
    report = contracts.check_current(changed, "1.8.0")
    assert report["broken"] == {}
    assert any("zz_new_optional" in change for change in report["compatible"][name])


@pytest.mark.parametrize("name", list(Contracts().tools))
def test_missing_tool_fails_closed_unless_it_is_an_option_tool(name):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    del changed[name]
    if is_option_tool(name):
        assert contracts.check_current(changed, "1.7.3")["broken"][name]
    else:
        with pytest.raises(Halt, match="unavailable tool"):
            contracts.check_current(changed, "1.7.3")


@pytest.mark.parametrize(
    "name", [name for name, tool in Contracts().tools.items() if "annotations" in tool]
)
def test_read_only_annotation_drift_is_breaking(name):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    changed[name]["annotations"]["readOnlyHint"] = False
    if is_option_tool(name):
        assert (
            "read-only annotation changed"
            in contracts.check_current(changed, "1.7.3")["broken"][name]
        )
    else:
        with pytest.raises(Halt, match="official schema drift"):
            contracts.check_current(changed, "1.7.3")


def test_widened_input_type_and_narrowed_output_type_are_compatible():
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    quantity = changed["place_equity_order"]["inputSchema"]["properties"]["quantity"]
    assert quantity.get("type") is not None
    quantity["type"] = sorted(
        {*([quantity["type"]] if isinstance(quantity["type"], str) else quantity["type"]), "null"}
    )
    report = contracts.check_current(changed, "1.7.3")
    assert "place_equity_order" in report["compatible"]


def test_description_only_changes_remain_ignored():
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    for tool in changed.values():
        tool["description"] = "Updated provider prose"
        tool["inputSchema"]["description"] = "Updated input prose"
        tool["outputSchema"]["description"] = "Updated output prose"
    contracts.check_current(changed, "1.7.3")


@pytest.mark.parametrize("old,new", [("1.6.2", "1.7.0"), ("1.7.0", "1.7.3")])
def test_real_past_robinhood_upgrades_would_have_kept_working(old, new):
    """Both historical upgrades needed a TradeAgent release under exact pinning."""
    load = lambda v: json.loads(  # noqa: E731
        files("tradeagent").joinpath(f"contracts/official-{v}.json").read_text()
    )
    contracts = Contracts()
    contracts.manifest = load(old)
    contracts.tools = contracts.manifest["tools"]
    report = contracts.check_current(load(new)["tools"], new)
    assert report["broken"] == {}
    assert report["compatible"]
