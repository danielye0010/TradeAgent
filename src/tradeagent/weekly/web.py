"""Public paper projection, independent publisher and localhost-only weekly view."""

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from .reporting import read_public

ASSETS = {
    "weekly.html": "text/html; charset=utf-8",
    "weekly.css": "text/css; charset=utf-8",
    "weekly.js": "text/javascript; charset=utf-8",
}


def asset(name):
    return files("tradeagent.weekly").joinpath("static", name).read_bytes()


def publish(directory, remote):
    from ..publish import _git

    directory = Path(directory)
    data = read_public(directory)
    work = directory / "public-checkout"
    if not (work / ".git").exists():
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch=gh-pages", remote, str(work)],
            capture_output=True,
            check=True,
            timeout=120,
        )
    else:
        _git(work, "pull", "--ff-only", "origin", "gh-pages")
    for name in ASSETS:
        (work / name).write_bytes(asset(name))
    (work / "weekly.json").write_text(json.dumps(data, allow_nan=False))
    # Preserve every existing file/record; add only a navigation link to the daily page.
    index = work / "index.html"
    if index.exists():
        text = index.read_text()
        if 'href="weekly.html"' not in text:
            text = text.replace(
                '<nav class="bar-nav" aria-label="Sections">',
                '<nav class="bar-nav" aria-label="Sections"><a href="weekly.html">Weekly / 周度持仓</a>',
                1,
            )
            index.write_text(text)
    names = [*ASSETS, "weekly.json"] + (["index.html"] if index.exists() else [])
    _git(work, "add", "--", *names)
    if _git(work, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return {"status": "UNCHANGED", "page": "weekly.html"}
    _git(
        work,
        "-c",
        "user.name=TradeAgent",
        "-c",
        "user.email=tradeagent@users.noreply.github.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "Update complete weekly SHADOW record",
    )
    _git(work, "push", "origin", "gh-pages")
    return {"status": "PUBLISHED", "page": "weekly.html", "start_date": data.get("start_date")}


def serve(directory, *, port=8643):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            host = self.headers.get("Host", "").split(":")[0]
            if host not in {"127.0.0.1", "localhost"}:
                self.send_error(403)
                return
            name = self.path.split("?")[0].lstrip("/") or "weekly.html"
            if name in ASSETS:
                body, kind = asset(name), ASSETS[name]
            elif name == "weekly.json":
                body, kind = json.dumps(read_public(directory)).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
