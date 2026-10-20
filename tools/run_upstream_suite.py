#!/usr/bin/env python3
"""Run upstream PDF Arranger's exporter golden-file suite against the vendored copy.

Why this exists
---------------
The vendored engine is claimed to be byte-identical to upstream.  A hash check
proves the *files* match; it does not prove the engine still behaves correctly
when imported through this package (with the headless ``gi`` shim in place) and
addressed with real file paths instead of the GUI's temporary copies.

This script closes that gap: it aliases the vendored modules as ``pdfarranger.*``,
points upstream's own ``tests/test_exporter.py`` at them, and runs the 29 golden
QDF comparisons.  If the shim, the path handling, or the module wiring changed
behaviour, these fail.

Usage
-----
    python tools/run_upstream_suite.py [--upstream-dir DIR]

``--upstream-dir`` defaults to ``.upstream/pdfarranger`` if present, else
``_probe12/pdfarranger-main`` in the parent workspace (the checkout used during
development).  Exits non-zero if the suite cannot be found or any test fails.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def find_upstream(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates += [
        REPO / ".upstream" / "pdfarranger",
        REPO.parent / "_probe12" / "pdfarranger-main",
    ]
    for cand in candidates:
        if (cand / "tests" / "test_exporter.py").is_file():
            return cand.resolve()
    # Exit code 3 is the "not available" signal the test suite turns into a skip;
    # anything else would be indistinguishable from a real failure.
    print(
        "upstream checkout not found. Fetch it first:\n"
        "    python tools/fetch_upstream.py\n"
        "or pass --upstream-dir pointing at a pdfarranger source tree that "
        "contains tests/test_exporter.py",
        file=sys.stderr,
    )
    raise SystemExit(3)


def install_aliases() -> None:
    """Expose the vendored modules under the ``pdfarranger.*`` names upstream uses."""
    sys.path.insert(0, str(REPO))
    from pdfarranger_api._engine import load_engine

    load_engine()  # installs the headless gi shim before anything imports core

    pkg = importlib.import_module("pdfarranger_api._vendor.pdfarranger")
    for name in ("core", "exporter", "metadata", "exporter_outlines"):
        mod = importlib.import_module(f"pdfarranger_api._vendor.pdfarranger.{name}")
        setattr(pkg, name, mod)
        sys.modules[f"pdfarranger.{name}"] = mod
    sys.modules["pdfarranger"] = pkg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream-dir", default=None)
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="only print the summary line"
    )
    args = parser.parse_args(argv)

    upstream = find_upstream(args.upstream_dir)
    install_aliases()
    sys.path.insert(0, str(upstream))
    os.chdir(upstream)  # upstream's tests open fixtures by relative path

    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_exporter")
    verbosity = 0 if args.quiet else 2
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)

    print(
        f"upstream golden suite: run={result.testsRun} "
        f"failures={len(result.failures)} errors={len(result.errors)}"
    )
    if result.testsRun == 0:
        print("error: no tests were collected", file=sys.stderr)
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
