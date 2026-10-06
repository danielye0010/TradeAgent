"""Check reusable project links, deployment integrity, and source-artifact privacy."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(files):
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def check():
    errors = []
    manifest = json.loads((ROOT / "docs/FRAMEWORK_FREEZE.json").read_text())
    for name, expected in manifest["files"].items():
        path = ROOT / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"deployment hash mismatch: {name}")
    sources = {p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")}
    frozen_sources = {n for n in manifest["files"] if n.startswith("src/") and n.endswith(".py")}
    if sources != frozen_sources or fingerprint(manifest["files"]) != manifest["framework_sha256"]:
        errors.append("deployment manifest source set/fingerprint mismatch")
    provenance = json.loads((ROOT / "docs/UPSTREAM_PROVENANCE.json").read_text())
    for category in ("files", "licenses"):
        for name, expected in provenance[category].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                errors.append(f"upstream provenance mismatch: {name}")

    # Restrict the scan to source artifacts: never enumerate/open operator runtime data.
    paths = [
        ROOT / n
        for n in (
            "README.md",
            "AGENTS.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "THIRD_PARTY.md",
            "pyproject.toml",
            "GPT_HANDOFF.md",
        )
        if (ROOT / n).is_file()
    ]
    for directory in ("docs", "config", "src", "tests", "scripts", ".github"):
        paths.extend(
            p
            for p in (ROOT / directory).rglob("*")
            if p.is_file()
            and p.suffix in {".md", ".py", ".json", ".yml"}
            and "__pycache__" not in p.parts
            and ".local." not in p.name
            and p.name not in {"config.json", "risk.json"}
            and (directory != "config" or ".example." in p.name or ".template." in p.name)
        )
    key_marker = "-----BEGIN " + r"(?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    token_pattern = r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    personal_path = r"(?:/home/[A-Za-z0-9_.-]+/|[A-Z]:[\\/](?:Users|Documents)[\\/])"
    for path in paths:
        text = path.read_text()
        for pattern, label in (
            (key_marker, "private signing key"),
            (token_pattern, "credential token"),
            (personal_path, "personal machine path"),
        ):
            if re.search(pattern, text):
                errors.append(f"{label}: {path.relative_to(ROOT)}")
        if path.suffix != ".md":
            continue
        for target in re.findall(r"\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
            if "://" in target or target.startswith("mailto:"):
                continue
            file_name, _, anchor = unquote(target).partition("#")
            destination = (path.parent / file_name).resolve() if file_name else path
            if not destination.exists():
                errors.append(f"broken link: {path.relative_to(ROOT)} -> {target}")
            elif anchor and destination.suffix == ".md":
                headings = re.findall(r"^#+\s+(.+)$", destination.read_text(), re.M)
                slugs = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
                if anchor not in slugs:
                    errors.append(f"broken anchor: {path.relative_to(ROOT)} -> {target}")
    if errors:
        raise SystemExit("\n".join(errors))
    print("Project links, deployment manifest, provenance and source-artifact scan passed.")


if __name__ == "__main__":
    check()
