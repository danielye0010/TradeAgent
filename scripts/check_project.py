"""Check documentation and upstream provenance; optionally refresh legacy manifest."""

import argparse
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


def update_manifest():
    """Hash source artifacts without reading operator config or runtime state."""
    paths = [ROOT / name for name in (".gitignore", "LICENSE", "pyproject.toml")]
    paths.extend((ROOT / "src").rglob("*.py"))
    paths.extend((ROOT / "src").glob("*/contracts/*.json"))
    paths.extend((ROOT / "tests").rglob("*.py"))
    paths.extend((ROOT / "config").glob("*.example.json"))
    paths.extend((ROOT / "config").glob("*.template.json"))
    paths.extend((ROOT / "licenses").glob("*.txt"))
    files = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
        if "__pycache__" not in path.parts
    }
    manifest_path = ROOT / "docs/deployment_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(files=files, framework_sha256=fingerprint(files))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def check():
    errors = []
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
    print("Project checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-manifest", action="store_true", help="regenerate deployment file hashes"
    )
    if parser.parse_args().update_manifest:
        update_manifest()
    check()
