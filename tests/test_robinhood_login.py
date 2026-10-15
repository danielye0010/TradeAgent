"""Browser sign-in against a local fake OAuth server; no Robinhood network access."""

import base64
import hashlib
import http.server
import json
import os
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import pytest

from tradeagent import robinhood_login as rl
from tradeagent.standalone_mcp import ExternalOAuthToken


class FakeAuthorizationServer:
    """Minimal RFC 8414 / 7591 / 6749+PKCE server recording every request."""

    def __init__(self, expires_in=3600):
        self.expires_in = expires_in
        self.registrations, self.token_requests = [], []
        self.challenge = None
        self.serial = 0
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, document, code=200):
                body = json.dumps(document).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self.reply(
                    {
                        "issuer": server.base + "/mcp/trading",
                        "authorization_endpoint": server.base + "/oauth",
                        "token_endpoint": server.base + "/token",
                        "registration_endpoint": server.base + "/register",
                        "code_challenge_methods_supported": ["S256"],
                    }
                )

            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/register":
                    server.registrations.append(json.loads(raw))
                    self.reply({"client_id": "client-1"}, 201)
                    return
                form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                server.token_requests.append(form)
                if form["grant_type"] == "authorization_code":
                    digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                    if form["code"] != "the-code" or expected != server.challenge:
                        self.reply({"error": "invalid_grant"}, 400)
                        return
                elif form["refresh_token"] != f"refresh-{server.serial}":
                    self.reply({"error": "invalid_grant"}, 400)
                    return
                server.serial += 1
                document = {
                    "access_token": f"access-{server.serial}",
                    "refresh_token": f"refresh-{server.serial}",
                    "expires_in": server.expires_in,
                }
                self.reply(document)

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    fake = FakeAuthorizationServer()
    monkeypatch.setattr(rl, "_check_url", lambda url, hosts: url)
    yield fake
    fake.close()


def free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def browser(server, issuer=None, state_override=None):
    """Play the user's browser: approve and follow the redirect back to TradeAgent."""

    def emit(line):
        if "/oauth?" not in line:
            return
        query = parse_qs(urlsplit(line.strip()).query)
        server.challenge = query["code_challenge"][0]
        assert query["code_challenge_method"] == ["S256"]
        assert query["resource"] == [rl.RESOURCE]
        state = state_override or query["state"][0]
        iss = issuer or server.base + "/mcp/trading"
        target = f"{query['redirect_uri'][0]}?code=the-code&state={state}&iss={iss}"
        threading.Thread(target=lambda: urlopen(target, timeout=5).read(), daemon=True).start()

    return emit


def test_login_saves_owner_only_session_and_helper_serves_contract(server, tmp_path, monkeypatch):
    store = tmp_path / "config" / "tradeagent" / "robinhood-oauth.json"
    saved = rl.login(
        port=free_port(),
        open_browser=False,
        timeout=10,
        path=store,
        discovery=server.base + "/meta",
        emit=browser(server),
    )
    assert saved == store
    assert oct(store.stat().st_mode & 0o777) == "0o600"
    assert oct(store.parent.stat().st_mode & 0o777) == "0o700"
    registration = server.registrations[0]
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration["redirect_uris"][0].startswith("http://localhost:")
    assert server.token_requests[0]["resource"] == rl.RESOURCE

    # The installed helper satisfies the engine's existing external-helper contract.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    helper = tmp_path / "libexec" / "robinhood-mcp-oauth-helper"
    assert rl.install_helper(helper, python=sys.executable)
    assert oct(helper.stat().st_mode & 0o777) == "0o700"
    token = ExternalOAuthToken(helper, tmp_path / "unrelated-repo")()
    assert token == "access-1"


def test_expired_session_refreshes_once_and_rotates(server, tmp_path):
    store = tmp_path / "robinhood-oauth.json"
    rl.login(
        port=free_port(),
        open_browser=False,
        timeout=10,
        path=store,
        discovery=server.base + "/meta",
        emit=browser(server),
    )
    first = rl.current_token(store)
    assert first["access_token"] == "access-1"
    assert len(server.token_requests) == 1  # still fresh: no refresh call

    later = json.loads(store.read_text())["expires_at"] - 10
    refreshed = rl.current_token(store, now=later)
    assert refreshed["access_token"] == "access-2"
    assert refreshed["resource"] == rl.RESOURCE
    assert later + 30 < refreshed["expires_at"] <= later + rl.MAX_REPORTED_LIFETIME
    assert json.loads(store.read_text())["refresh_token"] == "refresh-2"
    assert oct(store.stat().st_mode & 0o777) == "0o600"


def test_reported_expiry_stays_inside_engine_window(server, tmp_path):
    server.expires_in = 30 * 86400
    store = tmp_path / "robinhood-oauth.json"
    rl.login(
        port=free_port(),
        open_browser=False,
        timeout=10,
        path=store,
        discovery=server.base + "/meta",
        emit=browser(server),
    )
    token = rl.current_token(store, now=1_000_000.0)
    assert token["expires_at"] <= 1_000_000 + 86400 - 3600


@pytest.mark.parametrize("bad", ["state", "issuer"])
def test_mismatched_callback_is_rejected_without_token_exchange(server, tmp_path, bad):
    with pytest.raises(rl.LoginError):
        rl.login(
            port=free_port(),
            open_browser=False,
            timeout=10,
            path=tmp_path / "s.json",
            discovery=server.base + "/meta",
            emit=browser(
                server,
                issuer="https://evil.example" if bad == "issuer" else None,
                state_override="forged" if bad == "state" else None,
            ),
        )
    assert server.token_requests == []
    assert not (tmp_path / "s.json").exists()


def test_untrusted_endpoints_are_refused():
    with pytest.raises(rl.LoginError, match="untrusted"):
        rl._check_url("https://robinhood.com.evil.example/token", rl.ALLOWED_HOSTS)
    with pytest.raises(rl.LoginError, match="untrusted"):
        rl._check_url("http://api.robinhood.com/oauth2/token/", rl.ALLOWED_HOSTS)
    assert rl._check_url("https://api.robinhood.com/oauth2/token/", rl.ALLOWED_HOSTS)


def test_custom_helper_is_preserved_and_logout_only_removes_ours(tmp_path):
    custom = tmp_path / "helper"
    custom.write_text("#!/bin/sh\necho mine\n")
    assert rl.install_helper(custom) is False
    assert custom.read_text() == "#!/bin/sh\necho mine\n"
    store = tmp_path / "store.json"
    store.write_text("{}")
    assert rl.logout(store, custom) == [str(store)]
    assert custom.exists()

    ours = tmp_path / "ours"
    assert rl.install_helper(ours)
    assert str(ours) in rl.logout(tmp_path / "missing.json", ours)
    assert not ours.exists()


def test_missing_session_and_loose_permissions_fail_clearly(tmp_path):
    with pytest.raises(rl.LoginError, match="tradeagent login"):
        rl.current_token(tmp_path / "absent.json")
    loose = tmp_path / "loose.json"
    loose.write_text("{}")
    os.chmod(loose, 0o644)
    with pytest.raises(rl.LoginError, match="owner-only"):
        rl.current_token(loose)


def test_logout_waits_for_in_flight_refresh_and_wins(tmp_path):
    store = tmp_path / "robinhood-oauth.json"
    rl._write_private(store, {"access_token": "a", "expires_at": 9e12, "refresh_token": "r"})
    finished = threading.Event()
    with rl._locked(store):  # a helper mid-refresh holds the lock
        worker = threading.Thread(target=lambda: (rl.logout(store, tmp_path / "h"), finished.set()))
        worker.start()
        assert not finished.wait(0.3)
        rl._write_private(store, {"access_token": "b", "expires_at": 9e12, "refresh_token": "r2"})
    worker.join(5)
    assert finished.is_set() and not store.exists()
    with pytest.raises(rl.LoginError, match="tradeagent login"):
        rl.current_token(store)
