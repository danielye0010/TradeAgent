"""Validate release versions, source/package contents and SHA-256 distribution checksums."""

import argparse
import hashlib
import re
import subprocess
import tarfile
import tomllib
import zipfile
from email import message_from_bytes
from pathlib import Path, PurePosixPath

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
TOKENS = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    rb"|AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{32,}"
    rb"|eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})"
)
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".jsonl"}
FORBIDDEN_PARTS = {".git", ".venv", "work", "data", ".tokens", "__pycache__"}
FORBIDDEN_NAMES = {"GPT_HANDOFF.md", "config.json", "risk.json", "tradeagent.toml"}


def scan(name, content):
    if name == "data/.gitkeep":
        return
    path = PurePosixPath(name)
    if (
        path.suffix in FORBIDDEN_SUFFIXES
        or FORBIDDEN_PARTS.intersection(path.parts)
        or path.name in FORBIDDEN_NAMES
        or path.name == ".env"
        or (path.name.startswith(".env.") and ".example." not in path.name)
        or ".local." in path.name
        or re.search(r"(?:^|[._-])(?:credentials?|tokens?)(?:[._-]|$)", path.name)
        or PRIVATE_KEY.search(content)
        or TOKENS.search(content)
    ):
        raise ValueError(f"private/runtime artifact or credential pattern: {name}")


def check(tag=None, dist=None):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    version = Version(project["version"])
    if str(version) != project["version"] or version.local or version.dev or version.post:
        raise ValueError("release version must be canonical, without local/dev/post metadata")
    release_tag = "v" + version.base_version
    if version.pre:
        stage, number = version.pre
        label = {"a": "alpha", "b": "beta", "rc": "rc"}[stage]
        release_tag += f"-{label}.{number}"
    if tag is not None and tag != release_tag:
        raise ValueError(f"tag/version mismatch: expected {release_tag}, got {tag}")

    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    tracked = {name for name in tracked if name}
    for name in sorted(tracked):
        scan(name, (ROOT / name).read_bytes())
    if dist is None:
        print(f"Source release checks passed: {version}, {release_tag}, {len(tracked)} files")
        return

    artifacts = [
        dist / f"tradeagent-{version}-py3-none-any.whl",
        dist / f"tradeagent-{version}.tar.gz",
    ]
    if set(dist.glob("*.whl")) | set(dist.glob("*.tar.gz")) != set(artifacts):
        raise ValueError("dist must contain exactly the current wheel and source distribution")
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            metadata_name = f"tradeagent-{version}.dist-info/METADATA"
            for name in members:
                if name.startswith("tradeagent/"):
                    source = "src/" + name
                    if source not in tracked or members[name] != (ROOT / source).read_bytes():
                        raise ValueError(f"unexpected or changed wheel source: {name}")
                elif not name.startswith(f"tradeagent-{version}.dist-info/"):
                    raise ValueError(f"unexpected wheel member: {name}")
            required = {
                "tradeagent/owner.example.toml",
                "tradeagent/contracts/official-1.7.0.json",
            }
        else:
            prefix = f"tradeagent-{version}/"
            with tarfile.open(artifact) as archive:
                members = {}
                for member in archive.getmembers():
                    if member.isdir() and member.name == prefix.rstrip("/"):
                        continue
                    if not member.name.startswith(prefix) or not (
                        member.isdir() or member.isfile()
                    ):
                        raise ValueError(f"unexpected source archive member: {member.name}")
                    if member.isfile():
                        name = member.name.removeprefix(prefix)
                        if (
                            name not in tracked
                            and name != "setup.cfg"
                            and not name.startswith("src/tradeagent.egg-info/")
                            and name != "PKG-INFO"
                        ):
                            raise ValueError(f"untracked source archive member: {name}")
                        members[name] = archive.extractfile(member).read()
                        if name in tracked and members[name] != (ROOT / name).read_bytes():
                            raise ValueError(f"changed source archive member: {name}")
            metadata_name = "PKG-INFO"
            required = {"README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md"}
        if not required.issubset(members):
            raise ValueError(f"missing required package files: {required - members.keys()}")
        metadata = message_from_bytes(members[metadata_name])
        if metadata["Name"] != "tradeagent" or metadata["Version"] != str(version):
            raise ValueError(f"artifact metadata mismatch: {artifact.name}")
        for name, content in members.items():
            scan(name, content)
        print(f"Package content checks passed: {artifact.name} ({len(members)} files)")
    sums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in artifacts
    )
    (dist / "SHA256SUMS").write_text(sums)
    print(f"Release checks passed: {version}, {release_tag}; SHA256SUMS written")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Git tag to compare with Python package metadata")
    parser.add_argument(
        "--dist", type=Path, help="validate built distributions and write SHA256SUMS"
    )
    args = parser.parse_args()
    check(args.tag, args.dist)
