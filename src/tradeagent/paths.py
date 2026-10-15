"""Per-user default locations, so a normal setup needs no path editing."""

import os
from pathlib import Path


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tradeagent"


def state_home():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "tradeagent"


def default_config():
    return config_dir() / "tradeagent.toml"


def default_helper():
    return Path.home() / ".local/libexec/robinhood-mcp-oauth-helper"


def default_live_state():
    return state_home() / "live"
