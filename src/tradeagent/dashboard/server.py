"""`tradeagent dashboard`: a local, read-only web view of what TradeAgent is doing.

The server binds to the loopback interface, answers GET requests only, and
rejects requests whose Host header is not local (DNS-rebinding protection). It
reads state files; it never places orders, changes settings or starts jobs.
"""

import argparse
import hashlib
import json
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata, resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..paths import default_config, default_live_state
from . import jobs as job_reader
from . import readers

DEFAULT_PORT = 8642
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}
RUNS_IN_OVERVIEW = 90
ACTIVITY_ITEMS = 60


def version():
    try:
        return metadata.version("tradeagent")
    except metadata.PackageNotFoundError:
        return "dev"


def market_clock(now):
    from ..calendar import session_bounds

    today = datetime.fromtimestamp(now, readers.NY).date()
    try:
        bounds = session_bounds(today)
        day, upcoming = today, None
        for _ in range(10):
            day += timedelta(days=1)
            found = session_bounds(day)
            if found:
                upcoming = {"date": day.isoformat(), "open": found[0], "close": found[1]}
                break
    except Exception:  # calendar data missing must not break the page
        return {"date": today.isoformat(), "session": None, "next": None}
    session = {"open": bounds[0], "close": bounds[1]} if bounds else None
    return {"date": today.isoformat(), "session": session, "next": upcoming}


class Workspace:
    """Which state directories the dashboard reads, and the views built from them."""

    def __init__(
        self,
        state_dirs=(),
        *,
        demo=False,
        auto=True,
        live_dir=None,
        config_path=None,
        unit_dir=None,
        doctor=True,
    ):
        self.demo = demo
        self.auto = auto and not demo
        self.state_dirs = [Path(p).expanduser() for p in state_dirs]
        self.config_path = Path(config_path).expanduser() if config_path else default_config()
        self.live_dir = (
            Path(live_dir).expanduser()
            if live_dir
            else readers.configured_live_dir(self.config_path, default_live_state())
        )
        self.unit_dir = unit_dir
        self.doctor = doctor
        self._doctor = {"at": 0.0, "value": None}
        self._doctor_lock = threading.Lock()

    def jobs(self):
        if not self.auto:
            return []
        return job_reader.cached_discover(unit_dir=self.unit_dir)

    def sources(self):
        seen, found = set(), []

        def add(path, origin, job=None, job_kind=None):
            path = Path(path).expanduser()
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                return
            seen.add(key)
            found.append(
                {
                    "id": readers.source_id(path),
                    "path": path,
                    "kind": readers.kind_of(path),
                    "origin": origin,
                    "job": job,
                    "job_kind": job_kind,
                }
            )

        for path in self.state_dirs:
            add(path, "demo" if self.demo else "explicit")
        for job in self.jobs():
            if job["state_dir"]:
                add(job["state_dir"], "job", job["unit"], job["kind"])
        if self.auto:
            add(self.live_dir, "live")
        return found

    def setup_checks(self):
        if not self.doctor:
            return []
        with self._doctor_lock:
            if self._doctor["value"] is None or time.monotonic() - self._doctor["at"] > 60:
                from ..doctor import run

                try:
                    self._doctor["value"] = run()
                except Exception:  # the checklist is advisory
                    self._doctor["value"] = []
                self._doctor["at"] = time.monotonic()
            return self._doctor["value"]

    def overview(self):
        now = time.time()
        errors, research, executions, labs = [], [], [], []
        sources = self.sources()
        for source in sources:
            path, kind = source["path"], source["kind"]
            if source["job_kind"] not in (None, "daily"):
                continue  # v5/v6 collectors have their own formats; shown by timer status only
            try:
                if kind == "daily":
                    state = readers.daily_state(path)
                    state.update(id=source["id"], path=str(path), job=source["job"])
                    research.append(state)
                elif kind == "execution" or source["origin"] == "live":
                    for run in readers.execution_history(path):
                        run["origin"] = source["origin"]
                        executions.append(run)
                elif kind == "lab":
                    lab = readers.lab_state(path)
                    if lab:
                        lab.update(id=source["id"], path=str(path))
                        labs.append(lab)
            except Exception as error:  # one broken directory must not hide the rest
                errors.append({"path": str(path), "message": f"{type(error).__name__}: {error}"})
        executions.sort(key=lambda r: r.get("started") or 0, reverse=True)
        jobs = []
        for job in self.jobs():
            state = next((r for r in research if r.get("job") == job["unit"]), None)
            last = state["runs"][-1] if state and state["runs"] else None
            jobs.append(
                {
                    **job,
                    "source_id": state["id"] if state else None,
                    "last_session": {
                        "session_date": last["session_date"],
                        "status": last["status"],
                        "failure_kind": last["failure_kind"],
                    }
                    if last
                    else None,
                }
            )
        schedule = self.schedule(jobs, research)
        checks = self.setup_checks()
        live = None
        if not self.demo:
            live = {
                "settings": readers.live_settings(self.config_path),
                "kill_switch": (self.live_dir / "KILL").exists(),
                "state_dir": str(self.live_dir),
            }
        payload = {
            "generated_at": now,
            "version": version(),
            "demo": self.demo,
            "market": market_clock(now),
            "jobs": jobs,
            "schedule": schedule,
            "research": [self._trim(r) for r in research],
            "executions": executions[:50],
            "labs": labs,
            "live": live,
            "setup": {
                "checks": checks,
                "steps": self.steps(checks, jobs, research, executions, self.demo),
            },
            "activity": self.activity(research, executions, labs),
            "errors": errors,
            "orders_submitted_by_dashboard": 0,
        }
        return payload

    @staticmethod
    def _trim(state):
        trimmed = dict(state)
        trimmed["runs"] = state["runs"][-RUNS_IN_OVERVIEW:]
        trimmed["total_runs"] = len(state["runs"])
        return trimmed

    def schedule(self, jobs, research):
        marks = []
        for job in jobs:
            # Only daily research has decide/score/report steps; a disabled timer is not due.
            if job["kind"] != "daily" or (job["timer"] and not job["timer"]["enabled"]):
                continue
            for item in job["schedule"]:
                marks.append({**item, "protocol": job["protocol"], "unit": job["unit"]})
        if marks or any(j["kind"] == "daily" for j in jobs):
            return {"example": False, "marks": marks}
        protocols = [r["protocol"] for r in research] or ["daily-four-arm-v1"]
        for protocol in dict.fromkeys(protocols):
            for clock, role in readers.DEFAULT_SCHEDULE.get(protocol, []):
                hour, minute = map(int, clock.split(":"))
                marks.append(
                    {
                        "weekdays": True,
                        "start": hour * 60 + minute,
                        "end": hour * 60 + minute,
                        "protocol": protocol,
                        "role": role,
                    }
                )
        return {"example": True, "marks": marks}

    @staticmethod
    def steps(checks, jobs, research, executions, demo=False):
        status = {c["check"]: c["status"] for c in checks}
        return [
            {"id": "install", "done": True},
            {
                "id": "demo",
                "done": True if demo else None,
                "command": "tradeagent dashboard --demo",
            },
            {
                "id": "login",
                "done": status.get("Robinhood sign-in") == "ok"
                and status.get("Live settings") == "ok",
                "command": "tradeagent login",
            },
            {"id": "live_check", "done": None, "command": "tradeagent live-check"},
            {
                "id": "autopilot",
                "done": any(j["kind"] == "daily" for j in jobs),
                "command": (
                    'python scripts/install_shadow.py --daily --python "$PWD/.venv/bin/python" '
                    '--state-dir "$HOME/tradeagent-research/daily"'
                ),
            },
            {
                "id": "first_result",
                "done": any(
                    r["resolved_sessions"] and r["evidence_kind"] == "prospective" for r in research
                )
                or any(e["mode"] == "LIVE" for e in executions),
            },
        ]

    @staticmethod
    def activity(research, executions, labs):
        items = []
        for state in research:
            for run in state["runs"][-30:]:
                decision, outcome = run["decision"], run["outcome"]
                top = None
                if decision:
                    for c in decision["candidates"]:
                        price = (c.get("analysis") or {}).get("price")
                        if price and price.get("rank") == 1:
                            top = {
                                "symbol": c["symbol"],
                                "stance": price.get("stance"),
                                "thesis": price.get("thesis"),
                            }
                items.append(
                    {
                        "kind": "session",
                        "time": run["time"],
                        "source_id": state["id"],
                        "protocol": state["protocol"],
                        "evidence_kind": state["evidence_kind"],
                        "session_date": run["session_date"],
                        "status": run["status"],
                        "failure_kind": run["failure_kind"],
                        "decision_id": decision["decision_id"] if decision else None,
                        "picks": decision["arms"] if decision else None,
                        "top": top,
                        "outcome": outcome["arms"] if outcome else None,
                        "pending": run["pending"],
                    }
                )
        for run in executions[:20]:
            items.append(
                {
                    "kind": "trade",
                    "time": run.get("finished") or run.get("started"),
                    "run_id": run["id"],
                    "mode": run["mode"],
                    "synthetic": run["synthetic"],
                    "status": run["status"],
                    "symbol": next((o["symbol"] for o in run["orders"]), None),
                    "realized_pnl": run["realized_pnl"],
                    "in_progress": run["in_progress"],
                }
            )
        for lab in labs:
            if lab.get("last_learning"):
                items.append(
                    {
                        "kind": "lab",
                        "time": lab["last_learning"],
                        "source_id": lab["id"],
                        "learning_runs": lab["learning_runs"],
                        "predictions": lab["predictions"],
                        "synthetic": lab["synthetic"],
                    }
                )
        items.sort(key=lambda i: i.get("time") or 0, reverse=True)
        return items[:ACTIVITY_ITEMS]

    def run(self, source, decision_id):
        for item in self.sources():
            if item["id"] == source and item["kind"] == "daily":
                return readers.run_detail(item["path"], decision_id)
        return None

    def research_runs(self, source):
        for item in self.sources():
            if item["id"] == source and item["kind"] == "daily":
                return readers.daily_state(item["path"])["runs"]
        return None


def make_handler(workspace, port_ref):
    static_root = resources.files("tradeagent.dashboard") / "static"

    class Handler(BaseHTTPRequestHandler):
        server_version = "TradeAgentDashboard"
        sys_version = ""

        def log_message(self, format, *args):  # quiet by default
            return

        def _allowed_host(self):
            host = (self.headers.get("Host") or "").lower()
            port = port_ref[0]
            return host in {
                f"127.0.0.1:{port}",
                f"localhost:{port}",
                f"[::1]:{port}",
            }

        def _send(self, status, body, content_type, extra=None):
            self.send_response(status)
            for key, value in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(key, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, value, status=HTTPStatus.OK):
            body = json.dumps(value, default=str, allow_nan=False).encode()
            # The request time alone must not defeat caching.
            stable = {k: v for k, v in value.items() if k != "generated_at"}
            digest = hashlib.sha256(json.dumps(stable, default=str, sort_keys=True).encode())
            tag = '"' + digest.hexdigest()[:20] + '"'
            if self.headers.get("If-None-Match") == tag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("ETag", tag)
                self.end_headers()
                return
            self._send(
                status,
                body,
                "application/json",
                {"Cache-Control": "no-store", "ETag": tag},
            )

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._allowed_host():
                self._send(HTTPStatus.FORBIDDEN, b"forbidden host", "text/plain")
                return
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path in STATIC:
                    name, content_type = STATIC[url.path]
                    body = (static_root / name).read_bytes()
                    self._send(HTTPStatus.OK, body, content_type, {"Cache-Control": "no-cache"})
                elif url.path == "/api/overview":
                    self._json(workspace.overview())
                elif url.path == "/api/run":
                    found = workspace.run(
                        query.get("source", [""])[0], query.get("decision", [""])[0]
                    )
                    if found is None:
                        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    else:
                        self._json(found)
                elif url.path == "/api/runs":
                    found = workspace.research_runs(query.get("source", [""])[0])
                    if found is None:
                        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    else:
                        self._json({"runs": found})
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as error:
                self._json(
                    {"error": f"{type(error).__name__}: {error}"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def _reject(self):
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"read-only", "text/plain", {"Allow": "GET"})

        do_POST = do_PUT = do_DELETE = do_PATCH = _reject

    return Handler


def serve(workspace, port=DEFAULT_PORT, *, attempts=10):
    port_ref = [port]
    handler = make_handler(workspace, port_ref)
    last_error = None
    for candidate in range(port, port + attempts):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
        except OSError as error:
            last_error = error
            continue
        port_ref[0] = server.server_address[1]
        return server, port_ref[0]
    raise OSError(f"no free port from {port} to {port + attempts - 1}: {last_error}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tradeagent dashboard",
        description="Local read-only dashboard: what is running, what it decided, how it did.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--demo", action="store_true", help="show a synthetic workspace (no accounts needed)"
    )
    parser.add_argument(
        "--demo-dir",
        type=Path,
        default=None,
        help="where the demo workspace lives (default: ~/.cache/tradeagent/dashboard-demo)",
    )
    parser.add_argument("--rebuild-demo", action="store_true", help="regenerate the demo data")
    parser.add_argument(
        "--state-dir",
        type=Path,
        action="append",
        default=[],
        help="extra state directory to show (repeatable): daily research, paper/live run or lab",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="do not look for installed timers or the default live run",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    dirs = list(args.state_dir)
    if args.demo:
        from .demo import build

        demo_dir = args.demo_dir or Path.home() / ".cache/tradeagent/dashboard-demo"
        fresh = not (demo_dir / "demo-workspace.json").exists() or args.rebuild_demo
        if fresh:
            print(
                "Building the demo workspace (synthetic data, about a minute, only once)...",
                flush=True,
            )
        dirs = build(demo_dir, rebuild=args.rebuild_demo) + dirs
    workspace = Workspace(dirs, demo=args.demo, auto=not args.no_discover)
    server, port = serve(workspace, args.port)
    url = f"http://127.0.0.1:{port}/"
    print(f"TradeAgent dashboard: {url}")
    print("Read-only: it shows state and never places orders. Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
