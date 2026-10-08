"""Reviewed version-only upgrade; all subsequent structural drift fails closed."""

import copy
import json
from importlib.resources import files

import pytest

from tradeagent.model import Halt
from tradeagent.schema import Contracts


def test_reviewed_170_preserves_inputs_and_adds_authenticated_approval_outputs():
    contracts = Contracts()
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


@pytest.mark.parametrize("version", ["1.6.2", "1.7.1", "2.0.0", None])
def test_unreviewed_server_versions_fail_closed(version):
    contracts = Contracts()
    with pytest.raises(Halt, match="server version drift"):
        contracts.check_current(contracts.tools, version)


@pytest.mark.parametrize("name", list(Contracts().tools))
@pytest.mark.parametrize("field", ["inputSchema", "outputSchema"])
def test_every_required_schema_drift_fails_closed(name, field):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    changed[name][field]["additionalProperties"] = not changed[name][field].get(
        "additionalProperties", True
    )
    with pytest.raises(Halt, match="official schema drift"):
        contracts.check_current(changed, "1.7.0")


@pytest.mark.parametrize("name", list(Contracts().tools))
def test_missing_required_tool_fails_closed(name):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    del changed[name]
    with pytest.raises(Halt, match="unavailable tool"):
        contracts.check_current(changed, "1.7.0")


@pytest.mark.parametrize(
    "name", [name for name, tool in Contracts().tools.items() if "annotations" in tool]
)
def test_read_only_annotation_drift_fails_closed(name):
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    changed[name]["annotations"]["readOnlyHint"] = False
    with pytest.raises(Halt, match="official schema drift"):
        contracts.check_current(changed, "1.7.0")


def test_description_only_changes_remain_ignored():
    contracts = Contracts()
    changed = copy.deepcopy(contracts.tools)
    for tool in changed.values():
        tool["description"] = "Updated provider prose"
        tool["inputSchema"]["description"] = "Updated input prose"
        tool["outputSchema"]["description"] = "Updated output prose"
    contracts.check_current(changed, "1.7.0")
