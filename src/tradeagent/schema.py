"""Official broker contracts, checked for compatibility rather than exact equality.

TradeAgent ships a reviewed snapshot of the Robinhood MCP tool schemas. When the
live server differs, each tool TradeAgent relies on is compared structurally:

* Inputs must still accept every request TradeAgent can send under the reviewed
  schema (no new required fields, no removed fields, no narrowed types).
* Outputs must still be readable: every reviewed response field remains present,
  required and of a reviewed type. New response fields are ignored.
* A tool's read-only annotation must not change.

Identical or compatible changes keep working and are reported. Anything else
fails closed for that tool. A server version bump alone never blocks trading,
because every request is also validated against the live schema before sending.
"""

import json
from importlib.resources import files

from jsonschema import Draft202012Validator

from .model import Halt, digest

KNOWN_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items"}


def structural(value):
    if isinstance(value, dict):
        return {k: structural(v) for k, v in value.items() if k != "description"}
    if isinstance(value, list):
        return [structural(v) for v in value]
    return value


def _types(schema):
    kind = schema.get("type")
    if kind is None:
        return None
    return {kind} if isinstance(kind, str) else set(kind)


def _covers(outer, inner, path, problems, *, new_fields_ok):
    # Inputs: outer=live, inner=reviewed. Outputs: outer=reviewed, inner=live, and
    # new response fields (new_fields_ok) are ignored because TradeAgent never reads them.
    """Record why `outer` may reject a value that `inner` accepts."""
    if outer == inner:
        return
    if not isinstance(outer, dict) or not isinstance(inner, dict):
        problems.append(f"{path}: schema changed shape")
        return
    unknown = (set(outer) | set(inner)) - KNOWN_KEYWORDS
    for key in sorted(unknown):
        if outer.get(key) != inner.get(key):
            problems.append(f"{path}: '{key}' changed")
    outer_types, inner_types = _types(outer), _types(inner)
    if outer_types is not None and (inner_types is None or not inner_types <= outer_types):
        problems.append(f"{path}: type {sorted(inner_types or ['any'])} -> {sorted(outer_types)}")
    newly_required = set(outer.get("required", [])) - set(inner.get("required", []))
    if newly_required:
        verb = "no longer always returns" if new_fields_ok else "now requires"
        problems.append(f"{path}: {verb} {sorted(newly_required)}")
    outer_props, inner_props = outer.get("properties", {}), inner.get("properties", {})
    outer_open = outer.get("additionalProperties", True) is not False
    inner_open = inner.get("additionalProperties", True) is not False
    for name, sub in inner_props.items():
        if name in outer_props:
            _covers(outer_props[name], sub, f"{path}.{name}", problems, new_fields_ok=new_fields_ok)
        elif not outer_open and not new_fields_ok:
            problems.append(f"{path}.{name}: field removed")
    if new_fields_ok:
        # A reviewed response field the live server no longer declares may now be
        # absent or of any type; TradeAgent may read it, so treat removal as breaking.
        for name in sorted(set(outer_props) - set(inner_props)):
            problems.append(f"{path}.{name}: field removed")
    else:
        for name in sorted(set(outer_props) - set(inner_props)):
            if inner_open and outer_props[name] not in ({}, True):
                problems.append(f"{path}.{name}: newly constrained field")
        if inner_open and not outer_open:
            problems.append(f"{path}: no longer accepts extra fields")
    extra_outer = outer.get("additionalProperties", True)
    extra_inner = inner.get("additionalProperties", True)
    if isinstance(extra_outer, dict) and isinstance(extra_inner, dict):
        _covers(extra_outer, extra_inner, f"{path}.*", problems, new_fields_ok=new_fields_ok)
    if "items" in outer or "items" in inner:
        _covers(
            outer.get("items", {}),
            inner.get("items", {}),
            f"{path}[]",
            problems,
            new_fields_ok=new_fields_ok,
        )


def _added_fields(old, new, path, out):
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    for name in sorted(set(new_props) - set(old_props)):
        out.append(f"{path}.{name}")
    for name in sorted(set(old_props) & set(new_props)):
        _added_fields(old_props[name], new_props[name], f"{path}.{name}", out)
    if isinstance(old.get("items"), dict):
        _added_fields(old["items"], new.get("items"), f"{path}[]", out)


def compare_tool(reviewed, live):
    """Return (problems, changes) for one tool; empty problems means usable."""
    if not isinstance(live, dict):
        return ["tool no longer offered"], []
    live = structural(live)
    problems, changes = [], []
    reviewed_hint = reviewed.get("annotations", {}).get("readOnlyHint")
    if (
        reviewed_hint is not None
        and live.get("annotations", {}).get("readOnlyHint") != reviewed_hint
    ):
        problems.append("read-only annotation changed")
    if "inputSchema" in reviewed:
        _covers(
            live.get("inputSchema"), reviewed["inputSchema"], "input", problems, new_fields_ok=False
        )
        _added_fields(reviewed["inputSchema"], live.get("inputSchema"), "input", changes)
    if "outputSchema" in reviewed:
        live_output = live.get("outputSchema")
        if not isinstance(live_output, dict):
            problems.append("output: schema no longer published")
        else:
            _covers(reviewed["outputSchema"], live_output, "output", problems, new_fields_ok=True)
            _added_fields(reviewed["outputSchema"], live_output, "output", changes)
    return problems, [f"new optional {field}" for field in changes]


def is_option_tool(name):
    return "option" in name


class Contracts:
    def __init__(self):
        self.manifest = json.loads(
            files("tradeagent").joinpath("contracts/official-1.7.3.json").read_text()
        )
        self.tools = self.manifest["tools"]
        self.hash = digest(self.tools)
        self.report = None
        self.broken = {}

    def compatibility(self, tools, version):
        report = {
            "reviewed_version": self.manifest["server_version"],
            "server_version": version,
            "identical": [],
            "compatible": {},
            "broken": {},
        }
        for name, expected in self.tools.items():
            live = tools.get(name)
            if (
                isinstance(live, dict)
                and structural({k: live[k] for k in expected if k in live}) == expected
            ):
                report["identical"].append(name)
                continue
            problems, changes = compare_tool(expected, live)
            if problems:
                report["broken"][name] = problems
            else:
                report["compatible"][name] = changes or ["cosmetic schema change"]
        return report

    def check_current(self, tools, version, *, require_version=True, strict=False):
        """Fail closed only when a tool TradeAgent depends on changed incompatibly.

        The owner LIVE path is adaptive: a version bump with compatible schemas is
        reported in `self.report` instead of halting, and option tools that break
        only disable option features. `strict=True` keeps the original exact pin
        (reviewed version and identical schemas) for the legacy Codex/canary paths.
        """
        if strict:
            if require_version and version != self.manifest["server_version"]:
                raise Halt("broker server version drift; rediscovery and review required")
            for name, expected in self.tools.items():
                live = tools.get(name)
                if (
                    not isinstance(live, dict)
                    or structural({k: live[k] for k in expected if k in live}) != expected
                ):
                    raise Halt(f"official schema drift or unavailable tool: {name}")
            return None
        report = self.compatibility(tools, version)
        for name, problems in report["broken"].items():
            if is_option_tool(name):
                continue
            if problems == ["tool no longer offered"]:
                raise Halt(f"official schema drift or unavailable tool: {name}")
            raise Halt(
                f"official schema drift: Robinhood changed {name} incompatibly "
                f"({problems[0]}); update TradeAgent"
            )
        # Only a fully accepted catalog updates state; broken option tools stay disabled.
        self.report, self.broken = report, dict(report["broken"])
        return report

    def validate(self, name, value, direction="inputSchema"):
        if name not in self.tools:
            raise Halt("unsupported broker contract")
        if name in self.broken:
            raise Halt(f"official schema drift: {name} is unavailable until TradeAgent is updated")
        errors = list(Draft202012Validator(self.tools[name][direction]).iter_errors(value))
        if errors:
            # Schema error messages include supplied account values; do not log them.
            raise Halt(f"official {direction} validation failed for {name}")
        return value


def describe(report):
    """One-paragraph, user-facing summary of a compatibility report."""
    if not report:
        return None
    lines = []
    if report["server_version"] != report["reviewed_version"]:
        lines.append(
            f"Robinhood MCP is {report['server_version']} (TradeAgent was reviewed against "
            f"{report['reviewed_version']})."
        )
    if report["compatible"]:
        lines.append(f"Compatible changes in: {', '.join(sorted(report['compatible']))}.")
    if report["broken"]:
        lines.append(
            "Incompatible changes in: "
            + ", ".join(f"{n} ({p[0]})" for n, p in sorted(report["broken"].items()))
            + ". Run `pip install -U tradeagent` or pull the latest TradeAgent."
        )
    return " ".join(lines) or None
