"""Browser sign-in for the official Robinhood Trading MCP.

`tradeagent login` performs the standard MCP OAuth flow (RFC 9728/8414 discovery,
RFC 7591 dynamic client registration, authorization code with PKCE S256) through
the user's own browser. TradeAgent never sees the Robinhood password.

The resulting refresh token is stored owner-only under the user's config
directory, and a small helper executable is written to the location the rest of
TradeAgent already uses. The execution engine is unchanged: it still obtains
access tokens only by running that external helper, which prints the existing
`{"access_token", "expires_at", "resource"}` contract.
"""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import http.server
import json
import os
import secrets
import shlex
import socket
import stat
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .paths import config_dir, default_config, default_helper

RESOURCE = "https://agent.robinhood.com/mcp/trading"
DISCOVERY = "https://agent.robinhood.com/.well-known/oauth-authorization-server"
ALLOWED_HOSTS = ("robinhood.com",)
SCOPE = "internal"
CLIENT_NAME = "TradeAgent"
HELPER_MARKER = "# tradeagent-login-helper v1"
DEFAULT_PORT = 8765
REFRESH_MARGIN = 300
MAX_REPORTED_LIFETIME = 23 * 3600
DEFAULT_LIFETIME = 900


class LoginError(Exception):
    """A user-facing sign-in failure; never contains credential values."""


def store_path():
    return config_dir() / "robinhood-oauth.json"


# ---------------------------------------------------------------- HTTP helpers


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = build_opener(_NoRedirect)


def _check_url(url, allowed_hosts):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    trusted = any(host == h or host.endswith("." + h) for h in allowed_hosts)
    if parts.scheme != "https" or not trusted:
        raise LoginError(f"refusing untrusted OAuth endpoint: {parts.scheme}://{host}")
    return url


def _request(url, *, form=None, body=None, allowed_hosts=ALLOWED_HOSTS, timeout=20):
    _check_url(url, allowed_hosts)
    headers = {"Accept": "application/json", "User-Agent": "tradeagent-login"}
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        with _OPENER.open(Request(url, data=data, headers=headers), timeout=timeout) as response:
            payload = response.read(1 << 20)
    except HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            error = json.loads(exc.read(4096))
            detail = ": " + str(error.get("error_description") or error.get("error") or "")[:200]
        raise LoginError(f"Robinhood returned HTTP {exc.code}{detail}") from None
    except (URLError, OSError) as exc:
        raise LoginError(f"cannot reach Robinhood ({exc.__class__.__name__})") from None
    try:
        result = json.loads(payload)
    except ValueError:
        raise LoginError("Robinhood returned a non-JSON response") from None
    if not isinstance(result, dict):
        raise LoginError("Robinhood returned an unexpected response")
    return result


def discover(discovery=DISCOVERY, allowed_hosts=ALLOWED_HOSTS):
    meta = _request(discovery, allowed_hosts=allowed_hosts)
    needed = ("issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint")
    if any(not isinstance(meta.get(k), str) for k in needed):
        raise LoginError("Robinhood OAuth metadata is incomplete")
    if "S256" not in meta.get("code_challenge_methods_supported", ["S256"]):
        raise LoginError("Robinhood no longer advertises PKCE S256; update TradeAgent")
    for key in needed[1:]:
        _check_url(meta[key], allowed_hosts)
    return meta


# ---------------------------------------------------------------- token store


def _write_private(path, document):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(document, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_private(path):
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise LoginError(f"{path} must be owner-only (chmod 600)")
    return json.loads(path.read_text())


@contextlib.contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)


def _absorb(document, tokens, now):
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        raise LoginError("Robinhood did not return an access token")
    lifetime = tokens.get("expires_in")
    if not isinstance(lifetime, (int, float)) or lifetime <= 0:
        lifetime = DEFAULT_LIFETIME
    document["access_token"] = access
    document["expires_at"] = now + lifetime
    if isinstance(tokens.get("refresh_token"), str) and tokens["refresh_token"]:
        document["refresh_token"] = tokens["refresh_token"]
    document["updated_at"] = now
    return document


def current_token(path=None, now=None, allowed_hosts=ALLOWED_HOSTS):
    """Return the helper contract, refreshing under a lock when needed."""
    path = Path(path or store_path())
    if not path.exists():
        raise LoginError("not signed in; run `tradeagent login`")
    with _locked(path):
        if not path.exists():
            raise LoginError("not signed in; run `tradeagent login`")
        document = _read_private(path)
        now = time.time() if now is None else now
        if document.get("expires_at", 0) - now < REFRESH_MARGIN:
            if not document.get("refresh_token"):
                raise LoginError("session expired; run `tradeagent login` again")
            try:
                tokens = _request(
                    document["token_endpoint"],
                    form={
                        "grant_type": "refresh_token",
                        "refresh_token": document["refresh_token"],
                        "client_id": document["client_id"],
                        "resource": RESOURCE,
                    },
                    allowed_hosts=allowed_hosts,
                )
            except LoginError as error:
                raise LoginError(f"{error}; run `tradeagent login` again") from None
            document = _absorb(document, tokens, now)
            _write_private(path, document)
        expires_at = min(int(document["expires_at"]), int(now) + MAX_REPORTED_LIFETIME)
        return {
            "access_token": document["access_token"],
            "expires_at": expires_at,
            "resource": RESOURCE,
        }


# ---------------------------------------------------------------- browser flow


class _Callback(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):  # noqa: N802 - http.server API
        parts = urlsplit(self.path)
        if parts.path != "/callback":
            self.send_error(404)
            return
        type(self).result = parse_qs(parts.query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><title>TradeAgent</title><body style='font-family:sans-serif'>"
            b"<h2>TradeAgent is signed in.</h2><p>You can close this tab and return to the "
            b"terminal.</p></body>"
        )

    def log_message(self, *args):
        pass


class _Server(http.server.HTTPServer):
    def __init__(self, host, port, family):
        self.address_family = family
        super().__init__((host, port), _Callback)


def _listen(port):
    servers = []
    for host, family in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
        try:
            servers.append(_Server(host, port, family))
        except OSError:
            if family == socket.AF_INET:
                raise LoginError(f"port {port} is busy; retry with --port <another>") from None
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return servers


def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def _wait_for_code(paste, timeout, emit):
    if paste:
        emit("After approving, your browser may show an error page. Copy the full address")
        emit("from the address bar and paste it here:")
        line = sys.stdin.readline().strip()
        return parse_qs(urlsplit(line).query)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _Callback.result is not None:
            return _Callback.result
        time.sleep(0.2)
    raise LoginError("timed out waiting for the browser; retry with `tradeagent login --paste`")


def login(
    *,
    port=DEFAULT_PORT,
    paste=False,
    open_browser=True,
    timeout=300,
    path=None,
    discovery=DISCOVERY,
    allowed_hosts=ALLOWED_HOSTS,
    emit=print,
):
    path = Path(path or store_path())
    meta = discover(discovery, allowed_hosts)
    redirect_uri = f"http://localhost:{port}/callback"
    _Callback.result = None
    servers = [] if paste else _listen(port)
    try:
        client = _request(
            meta["registration_endpoint"],
            body={
                "client_name": CLIENT_NAME,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": SCOPE,
            },
            allowed_hosts=allowed_hosts,
        )
        client_id = client.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise LoginError("Robinhood did not register the TradeAgent client")
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(24)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": RESOURCE,
            }
        )
        url = meta["authorization_endpoint"] + "?" + query
        emit("Open this link to sign in to Robinhood and approve TradeAgent:\n")
        emit("  " + url + "\n")
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(url)
        reply = _wait_for_code(paste, timeout, emit)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    if reply.get("error"):
        raise LoginError("Robinhood sign-in was not approved: " + reply["error"][0][:100])
    if reply.get("state", [None])[0] != state:
        raise LoginError("sign-in response did not match this request; please retry")
    if "iss" in reply and reply["iss"][0] != meta["issuer"]:
        raise LoginError("sign-in response came from an unexpected issuer")
    code = reply.get("code", [None])[0]
    if not code:
        raise LoginError("sign-in response contained no authorization code")
    now = time.time()
    tokens = _request(
        meta["token_endpoint"],
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
        allowed_hosts=allowed_hosts,
    )
    document = {
        "format": 1,
        "resource": RESOURCE,
        "client_id": client_id,
        "token_endpoint": meta["token_endpoint"],
        "issuer": meta["issuer"],
        "created_at": now,
    }
    with _locked(path):
        _write_private(path, _absorb(document, tokens, now))
    return path


# ---------------------------------------------------------------- helper and config files


def helper_script(python=None):
    argv = [python or sys.executable, "-I", "-m", "tradeagent.robinhood_login", "token"]
    return f"#!/bin/sh\n{HELPER_MARKER}\nexec {shlex.join(argv)}\n"


def install_helper(path=None, force=False, python=None):
    """Write the helper unless a different, user-written helper already exists."""
    path = Path(path or default_helper())
    if path.exists() and HELPER_MARKER not in path.read_text(errors="replace") and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(helper_script(python))
    os.chmod(temporary, stat.S_IRWXU)
    os.replace(temporary, path)
    return True


def logout(path=None, helper=None):
    removed = []
    store = Path(path or store_path())
    # Delete under the same lock a refreshing helper holds, so an in-flight refresh
    # cannot recreate the session afterwards. The lock file itself stays.
    with _locked(store):
        if store.exists():
            store.unlink()
            removed.append(str(store))
    helper = Path(helper or default_helper())
    if helper.exists() and HELPER_MARKER in helper.read_text(errors="replace"):
        helper.unlink()
        removed.append(str(helper))
    return removed


STARTER_CONFIG = """\
# TradeAgent live settings (created by `tradeagent login`).
# Nothing here places an order. Real orders only happen when you run
# `tradeagent run-once --live` or explicitly ask the Codex skill for LIVE.

[live]
enabled = true
symbols = ["QQQ", "IWM"]   # the only symbols TradeAgent is allowed to buy
max_notional = "25"        # hard dollar ceiling for any single entry
# state_dir defaults to ~/.local/state/tradeagent/live

[broker]
timeout_seconds = 20
# oauth_helper = "{helper}"
# If several Robinhood accounts are visible, pick the agentic one:
# account_number = "YOUR_ACCOUNT_NUMBER"

[risk]
max_positions = 5
max_position_fraction = "0.20"
max_new_exposure_fraction = "0.10"
max_total_exposure_fraction = "1"
min_cash_fraction = "0.20"

[entry]
order_type = "market"
dollar_amount = "5"        # each live entry spends exactly this much

[exit]
order_type = "market"
hold_seconds = 3600        # sell automatically after one hour
polls = 3
session_buffer_seconds = 60
poll_interval_seconds = 5
risk_reduction_on_limits = true
"""


def write_starter_config(path=None, helper=None):
    """Create the per-user live config once; never overwrite the user's edits."""
    path = Path(path or default_config())
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    helper = Path(helper or default_helper())
    text = STARTER_CONFIG.format(helper=helper)
    if helper != default_helper():
        text = text.replace(f'# oauth_helper = "{helper}"', f'oauth_helper = "{helper}"')
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    return True


# ---------------------------------------------------------------- CLI


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tradeagent login",
        description="Sign in to the official Robinhood Trading MCP in your browser.",
    )
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("token", help=argparse.SUPPRESS)
    sub.add_parser("logout", help="forget the saved Robinhood session")
    parser.add_argument("--paste", action="store_true", help="paste the redirect URL manually")
    parser.add_argument("--no-browser", action="store_true", help="only print the sign-in link")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--helper", type=Path, help="helper location")
    parser.add_argument("--force", action="store_true", help="replace an existing custom helper")
    args = parser.parse_args(argv)
    try:
        if args.action == "token":
            print(json.dumps(current_token()))
            return 0
        if args.action == "logout":
            removed = logout(helper=args.helper)
            print("Signed out." if removed else "No saved Robinhood session.")
            return 0
        helper = args.helper or default_helper()
        saved = login(port=args.port, paste=args.paste, open_browser=not args.no_browser)
        installed = install_helper(helper, args.force)
        current_token()
        print(f"\nSigned in. Session saved to {saved} (owner-only).")
        if not installed:
            print(f"Kept your existing custom helper at {helper} (use --force to replace).")
        if write_starter_config(default_config(), helper):
            print(f"Created live settings at {default_config()}; review symbols and amounts.")
        print("\nNext: tradeagent live-check   (read-only; never places orders)")
        return 0
    except LoginError as error:
        print(f"tradeagent login: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
