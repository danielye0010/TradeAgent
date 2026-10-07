"""Current official structural contracts; every field is sourced from authenticated metadata."""

import json
from importlib.resources import files

from jsonschema import Draft202012Validator

from .model import Halt, digest


def structural(value):
    if isinstance(value, dict):
        return {k: structural(v) for k, v in value.items() if k != "description"}
    if isinstance(value, list):
        return [structural(v) for v in value]
    return value


class Contracts:
    def __init__(self):
        self.manifest = json.loads(
            files("tradeagent").joinpath("contracts/official-1.7.0.json").read_text()
        )
        self.tools = self.manifest["tools"]
        self.hash = digest(self.tools)

    def check_current(self, tools, version):
        if version != self.manifest["server_version"]:
            raise Halt("broker server version drift; rediscovery and review required")
        for name, expected in self.tools.items():
            live = tools.get(name)
            if (
                not isinstance(live, dict)
                or structural({k: live[k] for k in expected if k in live}) != expected
            ):
                raise Halt(f"official schema drift or unavailable tool: {name}")

    def validate(self, name, value, direction="inputSchema"):
        if name not in self.tools:
            raise Halt("unsupported broker contract")
        errors = list(Draft202012Validator(self.tools[name][direction]).iter_errors(value))
        if errors:
            # Schema error messages include supplied account values; do not log them.
            raise Halt(f"official {direction} validation failed for {name}")
        return value
