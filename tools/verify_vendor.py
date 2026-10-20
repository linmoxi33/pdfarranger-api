#!/usr/bin/env python3
"""Verify the vendored engine against its audited hashes.

``pdfarranger_api._engine`` already refuses to load a modified vendor file at
runtime.  This script is the build-time counterpart: it prints a per-file report
so a release check can show *what* is pinned, not just pass/fail.

Usage
-----
    python tools/verify_vendor.py            # human-readable report
    python tools/verify_vendor.py --json     # machine-readable
    python tools/verify_vendor.py --rehash   # print hashes to paste into _engine.py

Exit codes: 0 all match, 1 mismatch or missing file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pdfarranger_api._engine import UPSTREAM, VENDOR_HASHES  # noqa: E402

VENDOR_DIR = REPO / "pdfarranger_api" / "_vendor" / "pdfarranger"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect() -> list[dict]:
    rows = []
    for name, expected in sorted(VENDOR_HASHES.items()):
        path = VENDOR_DIR / name
        if not path.is_file():
            rows.append(
                {"file": name, "status": "missing", "expected": expected, "actual": None}
            )
            continue
        actual = sha256(path)
        rows.append(
            {
                "file": name,
                "status": "ok" if actual == expected else "MISMATCH",
                "expected": expected,
                "actual": actual,
                "bytes": path.stat().st_size,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--rehash",
        action="store_true",
        help="print current hashes in the form used by _engine.VENDOR_HASHES",
    )
    args = parser.parse_args(argv)

    rows = collect()

    if args.rehash:
        for row in rows:
            print(f'    "{row["file"]}": "{row["actual"]}",')
        return 0

    ok = all(r["status"] == "ok" for r in rows)

    if args.json:
        print(
            json.dumps(
                {
                    "upstream": UPSTREAM,
                    "vendor_dir": str(VENDOR_DIR),
                    "ok": ok,
                    "files": rows,
                },
                indent=2,
            )
        )
        return 0 if ok else 1

    print(f"upstream : {UPSTREAM['project']} {UPSTREAM['version']}")
    print(f"license  : {UPSTREAM['license']}")
    print(f"source   : {UPSTREAM['url']} ({UPSTREAM['tag_commit']})")
    print(f"vendored : {VENDOR_DIR}")
    print()
    width = max(len(r["file"]) for r in rows)
    for row in rows:
        mark = "OK  " if row["status"] == "ok" else row["status"].upper()
        print(f"  {mark} {row['file']:<{width}}  {str(row['actual'])[:16]}...")
    print()
    print("all vendored files match the audited hashes" if ok else "INTEGRITY FAILURE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
