"""Keep synthetic test state on the checkout's supported local filesystem."""


def pytest_configure(config):
    (config.rootpath / "work").mkdir(mode=0o700, exist_ok=True)
