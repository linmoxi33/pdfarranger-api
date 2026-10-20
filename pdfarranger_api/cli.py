"""Command line interface.

Four subcommands, all of which emit a single JSON object on stdout so the CLI can
be driven by a script without parsing prose:

``capabilities``
    What this build can do, its limits, and its error codes.

``inspect``
    Describe one or more PDFs (page count, sizes, encryption).

``plan``
    Validate a spec against real files and print exactly what would be written.
    Writes nothing.  This is the ``--dry-run`` path.

``apply``
    Validate and write.

Exit codes
----------
``0`` success, ``2`` bad usage/spec, ``3`` bad input, ``4`` refused (would
overwrite, etc.), ``5`` environment problem, ``6`` internal error.  The JSON
payload always carries ``ok`` and, on failure, ``error.code``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .api import capabilities, dry_run, inspect_pdf, run
from .errors import ApiError, ErrorCode, EXIT_OK

PROG = "pdfarranger-api"


def _emit(payload: dict[str, Any]) -> None:
    """Write one JSON object to stdout, UTF-8 safe on any console."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    stream = sys.stdout
    # Windows consoles default to the ANSI code page; JSON must survive intact.
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    stream.write(text + "\n")
    stream.flush()


def _load_spec(path: Path | None, inline: str | None) -> tuple[dict[str, Any], Path]:
    """Read a spec from a file or an inline JSON string.

    Returns the parsed object plus the directory used to resolve relative input
    paths: the spec's own directory, so a spec and its inputs can travel together.
    """
    if path is None and inline is None:
        raise ApiError(
            ErrorCode.SPEC_INVALID,
            "no spec supplied: pass --spec FILE or --json '{...}'",
        )
    if path is not None:
        if not path.exists():
            raise ApiError(
                ErrorCode.SPEC_INVALID,
                f"spec file not found: {path}",
                details={"path": str(path)},
            )
        raw_text = path.read_text(encoding="utf-8")
        base = path.parent
    else:
        raw_text = inline or ""
        base = Path.cwd()
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ApiError(
            ErrorCode.SPEC_INVALID,
            f"spec is not valid JSON: {exc.msg}",
            details={"line": exc.lineno, "column": exc.colno},
        ) from None
    if not isinstance(parsed, dict):
        raise ApiError(
            ErrorCode.SPEC_INVALID,
            "spec must be a JSON object",
            details={"got": type(parsed).__name__},
        )
    return parsed, base


def _cmd_capabilities(args: argparse.Namespace) -> int:
    _emit({"ok": True, "command": "capabilities", "result": capabilities()})
    return EXIT_OK


def _cmd_inspect(args: argparse.Namespace) -> int:
    results = []
    for raw in args.files:
        info = inspect_pdf(raw, password=args.password)
        results.append(info)
    payload: dict[str, Any] = {"ok": True, "command": "inspect"}
    payload["result"] = results[0] if len(results) == 1 else {"files": results}
    _emit(payload)
    return EXIT_OK


def _cmd_plan(args: argparse.Namespace) -> int:
    raw, base = _load_spec(Path(args.spec) if args.spec else None, args.json)
    if args.output:
        raw = {**raw, "output": args.output}
    plan_obj = dry_run(raw, base_dir=base)
    _emit({"ok": True, "command": "plan", "result": plan_obj.to_dict()})
    return EXIT_OK


def _cmd_apply(args: argparse.Namespace) -> int:
    raw, base = _load_spec(Path(args.spec) if args.spec else None, args.json)
    if args.output:
        raw = {**raw, "output": args.output}
    result = run(
        raw,
        base_dir=base,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    payload: dict[str, Any] = {
        "ok": True,
        "command": "apply",
        "dry_run": bool(args.dry_run),
        "result": result.to_dict(),
    }
    _emit(payload)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Headless API/CLI for PDF Arranger's page engine: merge, reorder, "
            "rotate, crop, scale and split PDFs without a display."
        ),
        epilog=(
            "Every subcommand prints one JSON object on stdout. "
            "Exit codes: 0 ok, 2 usage, 3 input, 4 refused, 5 environment, 6 internal."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"{PROG} {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_caps = sub.add_parser(
        "capabilities", help="print supported operations, limits and error codes"
    )
    p_caps.set_defaults(func=_cmd_capabilities)

    p_inspect = sub.add_parser("inspect", help="describe PDF files")
    p_inspect.add_argument("files", nargs="+", help="PDF file(s) to describe")
    p_inspect.add_argument(
        "--password", default=None, help="password for encrypted input(s)"
    )
    p_inspect.set_defaults(func=_cmd_inspect)

    for name, func, help_text in (
        ("plan", _cmd_plan, "validate a spec and print the resolved page plan"),
        ("apply", _cmd_apply, "validate a spec and write the output file(s)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--spec", metavar="FILE", help="path to a JSON spec file")
        p.add_argument(
            "--json", metavar="JSON", help="inline JSON spec (instead of --spec)"
        )
        p.add_argument(
            "-o", "--output", metavar="FILE", help="override the spec's output path"
        )
        if name == "apply":
            p.add_argument(
                "--overwrite",
                action="store_true",
                help="replace existing output files (default: refuse)",
            )
            p.add_argument(
                "--dry-run",
                action="store_true",
                help="validate only; do not write anything",
            )
        p.set_defaults(func=func)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ApiError as err:
        _emit(
            {
                "ok": False,
                "command": getattr(args, "command", None),
                "error": err.to_dict(),
            }
        )
        return err.exit_code
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:  # pragma: no cover - last-resort guard
        _emit(
            {
                "ok": False,
                "command": getattr(args, "command", None),
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": f"{type(exc).__name__}: {exc}",
                    "exit_code": 6,
                },
            }
        )
        return 6


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
