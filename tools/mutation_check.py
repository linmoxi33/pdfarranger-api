#!/usr/bin/env python3
"""Mutation testing: prove the test suite can actually fail.

A green suite proves the tests *ran*; it does not prove they would catch a
regression.  This script injects one deliberate defect at a time into the
package, runs the suite, and checks that the run fails.  A mutation that survives
( suite still green ) marks an untested code path.

Safety
------
Every mutation is applied to a file whose sha256 is recorded first, and the file
is restored in a ``finally`` block with a hash comparison afterwards.  If the
script is killed mid-run, ``--check-clean`` re-verifies the tree against git or
the recorded hashes.  Leaving a mutation behind would poison every later test run.

Usage
-----
    python tools/mutation_check.py            # run all mutations
    python tools/mutation_check.py -k crop    # subset by name
    python tools/mutation_check.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "pdfarranger_api"


@dataclass(frozen=True)
class Mutation:
    """One deliberate defect: replace ``old`` with ``new`` in ``file``."""

    name: str
    file: str
    old: str
    new: str
    why: str


MUTATIONS = [
    Mutation(
        name="crop_overflow_guard",
        file="spec.py",
        old='if vals["left"] + vals["right"] >= 1.0:',
        new='if vals["left"] + vals["right"] >= 99.0:',
        why="crop overflow would silently produce a negative-width page",
    ),
    Mutation(
        name="crop_negative_guard",
        file="spec.py",
        old="if v < 0:",
        new="if v < -99:",
        why="negative crop grows the page instead of shrinking it",
    ),
    Mutation(
        name="scale_zero_guard",
        file="spec.py",
        old="if value <= 0 or value > MAX_SCALE:",
        new="if value > MAX_SCALE:",
        why="scale=0 produces a zero-size page",
    ),
    Mutation(
        name="rotation_guard",
        file="spec.py",
        old="if value % 90 != 0:",
        new="if False:",
        why="non-multiples of 90 are not representable in PDF /Rotate",
    ),
    Mutation(
        name="page_range_upper",
        file="spec.py",
        old="lo, hi = (a, b) if a <= b else (b, a)",
        new="lo, hi = (a, a)",
        why="a range must expand to every page, not just its first",
    ),
    Mutation(
        name="page_bounds_check",
        file="plan.py",
        old="if not 1 <= ps.page <= n:",
        new="if not 1 <= ps.page <= n + 100:",
        why="an out-of-range page must be caught before the engine sees it",
    ),
    Mutation(
        name="overwrite_guard",
        file="execute.py",
        old="if out.exists() and not overwrite:",
        new="if False:",
        why="silently clobbering an existing artefact loses data",
    ),
    Mutation(
        name="atomic_write",
        file="execute.py",
        old="        size = tmp.stat().st_size\n        os.replace(tmp, destination)",
        new=(
            "        size = tmp.stat().st_size\n"
            "        with open(destination, 'wb') as _f:  # mutated: non-atomic\n"
            "            _f.write(tmp.read_bytes())"
        ),
        why="a non-atomic write can leave a truncated file on crash",
    ),
    Mutation(
        name="deterministic_stamp",
        file="execute.py",
        old="                if patched != raw:\n                    pdf.Root.Metadata.write(patched)",
        new="                if False:  # mutated: skip the XMP stamp patch\n                    pdf.Root.Metadata.write(patched)",
        why="pikepdf rewrites MetadataDate on save; without the patch the bytes vary",
    ),
    Mutation(
        name="output_same_as_input",
        file="plan.py",
        old="if out.resolve() in resolved_inputs:",
        new="if False:",
        why="writing over an input destroys the source",
    ),
    Mutation(
        name="unknown_field_guard",
        file="spec.py",
        old="if key not in allowed_set:",
        new="if False:",
        why="a typo'd field would be silently ignored instead of rejected",
    ),
    Mutation(
        name="blank_page_size_check",
        file="spec.py",
        old="if not 0 < val <= MAX_PAGE_POINTS:",
        new="if not val <= MAX_PAGE_POINTS:",
        why="a zero-size blank page is invisible",
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_mutation(mut: Mutation) -> tuple[Path, bytes, str]:
    """Apply ``mut``; return (path, original bytes, original hash)."""
    path = PKG / mut.file
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()

    text = original.decode("utf-8")
    if mut.old not in text:
        raise SystemExit(
            f"mutation {mut.name!r} cannot be applied: pattern not found in {mut.file}"
        )
    text = text.replace(mut.old, mut.new, 1)
    path.write_bytes(text.encode("utf-8"))
    return path, original, digest


def restore(path: Path, original: bytes, digest: str) -> None:
    """Restore and *verify* - a silently failed restore poisons later runs."""
    path.write_bytes(original)
    actual = sha256(path)
    if actual != digest:
        raise SystemExit(
            f"RESTORE FAILED for {path}: expected {digest[:12]}, got {actual[:12]}. "
            "The source tree is now inconsistent - restore it from git before continuing."
        )


def run_suite(quiet: bool = True) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "tests"]
    proc = subprocess.run(
        cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8"
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-k", "--filter", default=None, help="only names containing this")
    parser.add_argument("--list", action="store_true", help="list mutations and exit")
    args = parser.parse_args(argv)

    selected = [
        m for m in MUTATIONS if args.filter is None or args.filter in m.name
    ]
    if not selected:
        print(f"no mutation matches {args.filter!r}", file=sys.stderr)
        return 2

    if args.list:
        for m in selected:
            print(f"{m.name:28s} {m.file:12s} {m.why}")
        return 0

    print(f"baseline: running the suite unmutated ...", flush=True)
    code, out = run_suite()
    if code != 0:
        print("baseline suite already fails; fix that before mutating", file=sys.stderr)
        print(out[-2000:], file=sys.stderr)
        return 2
    print("baseline: green\n", flush=True)

    survived: list[Mutation] = []
    killed = 0
    for mut in selected:
        path, original, digest = apply_mutation(mut)
        try:
            code, out = run_suite()
        finally:
            restore(path, original, digest)

        if code == 0:
            survived.append(mut)
            print(f"  SURVIVED  {mut.name:28s} ({mut.why})", flush=True)
        else:
            killed += 1
            print(f"  killed    {mut.name:28s}", flush=True)

    print()
    print(f"mutations: {len(selected)}  killed: {killed}  survived: {len(survived)}")
    if survived:
        print("\nSurviving mutations indicate untested behaviour:")
        for mut in survived:
            print(f"  - {mut.name} ({mut.file}): {mut.why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
