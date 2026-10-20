"""Public Python API.

Two ways in:

Declarative (recommended for automation)
    Build a spec as a plain dict, hand it to :func:`run`.  Everything is
    validated before a byte is written.

Explicit (for callers that already have a page list)
    :func:`merge`, :func:`reorder`, :func:`split_pages` build the spec for you.

Both raise :class:`~pdfarranger_api.errors.ApiError` with a stable ``code``;
nothing else escapes.  Nothing here touches GTK - the package runs on a headless
box with only ``pikepdf`` installed.

Example
-------
>>> from pdfarranger_api import run
>>> result = run({
...     "inputs": [{"id": "a", "path": "report.pdf"}],
...     "pages": [
...         {"input": "a", "page": 1, "rotate": 90},
...         {"input": "a", "page": "2-4", "scale": 0.5},
...     ],
...     "output": "out.pdf",
... })
>>> result.page_count
4
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from . import __version__
from ._engine import GuiUnavailable, VendorIntegrityError, engine_probe, load_engine
from .errors import ApiError, ErrorCode
from .execute import ApplyResult, apply
from .plan import InputInfo, PageInfo, Plan, plan
from .spec import MAX_SCALE, SPEC_VERSION, Crop, Spec, parse_spec

__all__ = [
    "ApiError",
    "ApplyResult",
    "Crop",
    "ErrorCode",
    "GuiUnavailable",
    "InputInfo",
    "PageInfo",
    "Plan",
    "SPEC_VERSION",
    "Spec",
    "VendorIntegrityError",
    "apply",
    "capabilities",
    "inspect_pdf",
    "merge",
    "plan",
    "reorder",
    "run",
    "split_pages",
    "__version__",
]


def run(
    spec: Mapping[str, Any] | Spec,
    *,
    base_dir: str | Path | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ApplyResult:
    """Validate ``spec``, then write the output.  The one-call entry point."""
    parsed = spec if isinstance(spec, Spec) else parse_spec(spec, base_dir=base_dir)
    return apply(plan(parsed), overwrite=overwrite, dry_run=dry_run)


def dry_run(
    spec: Mapping[str, Any] | Spec,
    *,
    base_dir: str | Path | None = None,
) -> Plan:
    """Validate and resolve ``spec`` without writing anything."""
    parsed = spec if isinstance(spec, Spec) else parse_spec(spec, base_dir=base_dir)
    return plan(parsed)


def inspect_pdf(path: str | Path, password: str | None = None) -> dict[str, Any]:
    """Describe one PDF: page count, version, page sizes, encryption.

    Useful before writing a spec, and as a cheap health check in a pipeline.
    """
    import pikepdf

    from .plan import _open_input

    p = Path(path)
    doc = _open_input(p, password, "input")
    try:
        pages = []
        for i, page in enumerate(doc.pages, start=1):
            box = page.mediabox
            w = abs(float(box[2]) - float(box[0]))
            h = abs(float(box[3]) - float(box[1]))
            rotate = int(page.get("/Rotate", 0)) % 360
            pages.append(
                {
                    "index": i,
                    "width_pt": round(w, 3),
                    "height_pt": round(h, 3),
                    "width_mm": round(w * 25.4 / 72, 2),
                    "height_mm": round(h * 25.4 / 72, 2),
                    "rotate": rotate,
                }
            )
        return {
            "path": str(p),
            "page_count": len(doc.pages),
            "pdf_version": str(doc.pdf_version),
            "encrypted": bool(doc.is_encrypted),
            "size_bytes": p.stat().st_size,
            "pages": pages,
        }
    finally:
        doc.close()


def merge(
    paths: Sequence[str | Path],
    output: str | Path,
    *,
    overwrite: bool = False,
    deterministic: bool = False,
) -> ApplyResult:
    """Concatenate PDFs in order.

    Page counts are read up front so the spec stays explicit: an open-ended
    range would have to be resolved later, which makes the spec non-replayable.
    """
    inputs = [{"id": f"f{i}", "path": str(p)} for i, p in enumerate(paths, start=1)]
    pages: list[dict[str, Any]] = []
    for ref in inputs:
        count = inspect_pdf(ref["path"])["page_count"]
        pages.append({"input": ref["id"], "page": [1, count] if count == 1 else f"1-{count}"})
    return run(
        {
            "inputs": inputs,
            "pages": pages,
            "output": str(output),
            "deterministic": deterministic,
        },
        overwrite=overwrite,
    )


def reorder(
    path: str | Path,
    order: Sequence[int],
    output: str | Path,
    *,
    overwrite: bool = False,
    deterministic: bool = False,
) -> ApplyResult:
    """Rewrite one PDF with its pages in ``order`` (1-based, may repeat/omit)."""
    return run(
        {
            "inputs": [{"id": "src", "path": str(path)}],
            "pages": [{"input": "src", "page": list(order)}],
            "output": str(output),
            "deterministic": deterministic,
        },
        overwrite=overwrite,
    )


def split_pages(
    path: str | Path,
    output: str | Path,
    *,
    overwrite: bool = False,
    deterministic: bool = False,
) -> ApplyResult:
    """Write one file per page: ``out.pdf`` -> ``out-001.pdf``, ``out-002.pdf`` ...

    Page numbers are zero-padded to the width of the page count, so the files
    sort correctly in a directory listing.
    """
    return run(
        {
            "inputs": [{"id": "src", "path": str(path)}],
            "pages": [{"input": "src", "page": "1-"}],
            "output": str(output),
            "split": "page",
            "deterministic": deterministic,
        },
        overwrite=overwrite,
    )


def capabilities() -> dict[str, Any]:
    """Machine-readable description of what this build can do.

    Deliberately includes the *limits* alongside the features: a caller needs to
    know that ``scale`` stops at :data:`MAX_SCALE` and that crop fractions are
    per-side before it can build a valid spec.
    """
    probe = engine_probe()
    return {
        "name": "pdfarranger-api",
        "version": __version__,
        "spec_version": SPEC_VERSION,
        "upstream": probe["upstream"],
        "engine": {
            "loaded": probe["engine_loaded"],
            "gi_mode": probe["gi"].get("mode"),
            "cairo_mode": probe["graphics"]["cairo"].get("mode"),
            "pikepdf": probe["pikepdf"],
            "error": probe["error"],
        },
        "operations": [
            "merge",
            "reorder",
            "subset",
            "duplicate",
            "rotate",
            "crop",
            "scale",
            "blank_page",
            "overlay",
            "underlay",
            "split_per_page",
            "set_metadata",
            "encrypt_output",
        ],
        "limits": {
            "max_scale": MAX_SCALE,
            "crop_fraction_per_side": [0.0, 1.0],
            "crop_opposite_sides_must_sum_below": 1.0,
            "rotation_multiples_of": 90,
            "max_split_outputs": 512,
            "page_numbers": "1-based",
        },
        "error_codes": sorted(code.value for code in ErrorCode),
        "headless": True,
        "requires_gui": False,
    }
