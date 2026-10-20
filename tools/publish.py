#!/usr/bin/env python3
"""Publish this directory to GitHub via the Git Data API.

Why not ``git push``: HTTPS to github.com is frequently blocked on this machine
while ``gh api`` works, so the whole publish goes through the REST API.

Why a script rather than a handful of commands: the push has to be all-or-nothing
and it has to refuse to run if the active ``gh`` account is not the one that owns
the repository.  A mis-switched account silently pushes to someone else's
account, and the failure mode looks like a permissions error.

Usage
-----
    python tools/publish.py --dry-run     # list what would be sent
    python tools/publish.py               # push
    python tools/publish.py --verify      # push, then compare every blob back
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
OWNER = "linmoxi33"
REPO = "pdfarranger-api"
BRANCH = "main"

#: Everything tracked.  Explicit rather than "whatever is on disk" so a stray
#: build artefact or scratch file can never be published by accident.
INCLUDE = [
    ".gitattributes",
    ".gitignore",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "docs/architecture.png",
    "docs/corruption.png",
    "docs/hero.png",
    "examples/01_merge_reorder.py",
    "examples/02_crop_and_rotate.py",
    "examples/03_inspect_before_spec.py",
    "pdfarranger_api/__init__.py",
    "pdfarranger_api/__main__.py",
    "pdfarranger_api/_engine.py",
    "pdfarranger_api/_vendor/__init__.py",
    "pdfarranger_api/_vendor/pdfarranger/COPYING",
    "pdfarranger_api/_vendor/pdfarranger/__init__.py",
    "pdfarranger_api/_vendor/pdfarranger/core.py",
    "pdfarranger_api/_vendor/pdfarranger/exporter.py",
    "pdfarranger_api/_vendor/pdfarranger/exporter_outlines.py",
    "pdfarranger_api/_vendor/pdfarranger/metadata.py",
    "pdfarranger_api/api.py",
    "pdfarranger_api/cli.py",
    "pdfarranger_api/errors.py",
    "pdfarranger_api/execute.py",
    "pdfarranger_api/plan.py",
    "pdfarranger_api/spec.py",
    "samples/basic.pdf",
    "samples/encrypted.pdf",
    "samples/outlines.pdf",
    "tests/conftest.py",
    "tests/test_cli.py",
    "tests/test_engine.py",
    "tests/test_equivalence.py",
    "tests/test_export.py",
    "tests/test_spec.py",
    "tools/make_images.py",
    "tools/mutation_check.py",
    "tools/publish.py",
    "tools/run_upstream_suite.py",
    "tools/verify_vendor.py",
]

#: Text files get LF-normalised before hashing: ``.gitattributes`` declares
#: ``eol=lf``, so GitHub stores the LF form and a CRLF upload would show as
#: modified on the next checkout.
TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".cfg", ".ini", ".yml", ".yaml"}


def run(args: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def gh_api(endpoint: str | list[str], payload: dict | None = None) -> dict:
    """Call ``gh api`` with an optional JSON body.

    ``endpoint`` must stay a single string: unpacking it with ``*endpoint`` would
    spread it into characters ("repos/x" -> "r","e","p",...) and gh then reports
    "accepts 1 arg(s), received 41".  A list is accepted for the rare call that
    needs extra flags.

    Large bodies go through ``--input`` because passing a base64 blob on the
    command line hits Windows' ~32 KB argv limit (WinError 206).
    """
    parts = [endpoint] if isinstance(endpoint, str) else list(endpoint)
    cmd = ["gh", "api", *parts]
    if payload is None:
        out = run(cmd)
        return json.loads(out) if out.strip() else {}
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh)
        tmp = fh.name
    try:
        out = run(cmd + ["--input", tmp])
        return json.loads(out) if out.strip() else {}
    finally:
        Path(tmp).unlink(missing_ok=True)


def preflight() -> None:
    login = run(["gh", "api", "user", "--jq", ".login"]).strip()
    if login != OWNER:
        raise SystemExit(
            f"active gh account is {login!r}, expected {OWNER!r}. "
            f"Run: gh auth switch --user {OWNER}"
        )
    print(f"account : {login}")
    print(f"repo    : {OWNER}/{REPO}")


def read_file(rel: str) -> bytes:
    path = REPO_DIR / rel
    if not path.is_file():
        raise SystemExit(f"missing file: {rel}")
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true", help="compare blobs back")
    parser.add_argument("--message", default="feat: headless API, SDK and CLI for PDF Arranger")
    args = parser.parse_args(argv)

    preflight()

    files = {rel: read_file(rel) for rel in INCLUDE}
    total = sum(len(v) for v in files.values())
    print(f"files   : {len(files)} ({total / 1024:.1f} KiB)")

    if args.dry_run:
        for rel in sorted(files):
            print(f"  {len(files[rel]):>8}  {rel}")
        return 0

    # 1) blobs
    tree_entries = []
    uploaded: dict[str, str] = {}
    for i, rel in enumerate(sorted(files), start=1):
        data = files[rel]
        payload = {
            "content": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
        }
        result = gh_api(f"repos/{OWNER}/{REPO}/git/blobs", payload)
        sha = result["sha"]
        uploaded[rel] = sha
        tree_entries.append(
            {"path": rel, "mode": "100644", "type": "blob", "sha": sha}
        )
        print(f"  [{i:>2}/{len(files)}] {rel:<52} {sha[:10]}")

    # 2) tree on top of the existing one, so nothing already present is dropped
    ref = gh_api(f"repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    parent = ref["object"]["sha"]
    base_tree = gh_api(f"repos/{OWNER}/{REPO}/git/commits/{parent}")["tree"]["sha"]

    tree = gh_api(
        f"repos/{OWNER}/{REPO}/git/trees",
        {"base_tree": base_tree, "tree": tree_entries},
    )
    print(f"tree    : {tree['sha'][:10]}")

    # 3) commit
    commit = gh_api(
        f"repos/{OWNER}/{REPO}/git/commits",
        {
            "message": args.message,
            "tree": tree["sha"],
            "parents": [parent],
        },
    )
    print(f"commit  : {commit['sha'][:10]}")

    # 4) move the branch
    gh_api(
        f"repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": True},
    )
    print(f"ref     : {BRANCH} -> {commit['sha'][:10]}")

    if args.verify:
        print("\nverifying remote bytes ...")
        bad = 0
        for rel, local in sorted(files.items()):
            blob = gh_api(f"repos/{OWNER}/{REPO}/git/blobs/{uploaded[rel]}")
            remote = base64.b64decode(blob["content"])
            if remote != local:
                bad += 1
                print(f"  MISMATCH {rel} (local {len(local)}B, remote {len(remote)}B)")
            else:
                print(f"  ok {rel}")
        if bad:
            print(f"\n{bad} file(s) differ")
            return 1
        print(f"\nall {len(files)} files match byte-for-byte")

    print(f"\nhttps://github.com/{OWNER}/{REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
