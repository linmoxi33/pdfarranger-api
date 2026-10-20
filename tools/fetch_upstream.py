#!/usr/bin/env python3
"""Fetch the upstream PDF Arranger source, for the golden-file suite.

``tools/run_upstream_suite.py`` runs upstream's own 29 exporter tests against the
vendored engine.  Those tests live in the upstream source tree (upstream is not
published on PyPI), so a fresh clone has to fetch them once.

Downloads a tarball rather than cloning: ``git clone github.com`` is unreliable
on this network while ``codeload.github.com`` works.

Usage
-----
    python tools/fetch_upstream.py            # fetch into .upstream/pdfarranger
    python tools/fetch_upstream.py --force    # re-fetch even if present
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / ".upstream" / "pdfarranger"

#: Pinned to the same commit the vendored modules came from.  Floating on
#: ``main`` would make the golden suite fail for reasons unrelated to this
#: package the day upstream changes its fixtures.
REF = "main"
URL = f"https://codeload.github.com/pdfarranger/pdfarranger/tar.gz/refs/heads/{REF}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-download")
    args = parser.parse_args(argv)

    if (TARGET / "tests" / "test_exporter.py").is_file() and not args.force:
        print(f"already present: {TARGET}")
        return 0

    print(f"fetching {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=120) as resp:
            payload = resp.read()
    except Exception as exc:
        print(f"download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "If the network blocks codeload, download the tarball manually and "
            f"extract it to {TARGET}",
            file=sys.stderr,
        )
        return 1

    print(f"  {len(payload) / 1024:.0f} KiB")

    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = tar.getmembers()
        # Strip the single top-level directory the tarball wraps everything in.
        prefix = members[0].name.split("/")[0] + "/"
        for member in members:
            if not member.name.startswith(prefix):
                continue
            member.name = member.name[len(prefix):]
            if not member.name:
                continue
            tar.extract(member, path=TARGET)

    check = TARGET / "tests" / "test_exporter.py"
    if not check.is_file():
        print(f"extraction did not produce {check}", file=sys.stderr)
        return 1

    fixtures = len(list((TARGET / "tests" / "exporter").glob("*.pdf")))
    print(f"ok: {TARGET}")
    print(f"    {fixtures} golden fixtures")
    print("\nnow run:  python tools/run_upstream_suite.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
