"""Keep synthetic test state on the checkout's supported local filesystem."""

import pytest


def pytest_configure(config):
    (config.rootpath / "work").mkdir(mode=0o700, exist_ok=True)


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path_factory, monkeypatch):
    """Never let a test discover the developer's real config, session or helper."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
