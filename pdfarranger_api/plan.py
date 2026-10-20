"""Preflight: resolve a spec against real files, without writing anything.

``plan()`` is the read-only half of the API.  It opens every input, checks page
counts, applies the crop/scale/rotation arithmetic, and returns a
:class:`Plan` describing exactly what would be written.  ``apply()`` (in
:mod:`pdfarranger_api.execute`) then does the write.

The split matters for automation: a CI job can run ``plan`` and fail on a bad
spec without ever touching the output directory, and ``plan`` doubles as the
``--dry-run`` output of the CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import ApiError, ErrorCode, page_out_of_range
from .spec import MAX_PAGE_POINTS, PageSpec, Spec

__all__ = ["InputInfo", "PageInfo", "Plan", "plan"]


@dataclass(frozen=True)
class InputInfo:
    id: str
    path: Path
    page_count: int
    encrypted: bool
    pdf_version: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path),
            "page_count": self.page_count,
            "encrypted": self.encrypted,
            "pdf_version": self.pdf_version,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class PageInfo:
    """What one output page will look like, computed the way the engine does."""

    index: int
    source: str
    """``"a:3"`` for a slice of input ``a``, or ``"blank"``."""
    width: float
    height: float
    rotate: int
    scale: float
    crop: tuple[float, float, float, float]
    blank: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "source": self.source,
            "width_pt": round(self.width, 3),
            "height_pt": round(self.height, 3),
            "width_mm": round(self.width * 25.4 / 72, 2),
            "height_mm": round(self.height * 25.4 / 72, 2),
            "rotate": self.rotate,
            "scale": self.scale,
            "crop": list(self.crop),
            "blank": self.blank,
        }


@dataclass(frozen=True)
class Plan:
    """A validated, fully resolved description of the work to be done."""

    spec: Spec
    inputs: tuple[InputInfo, ...]
    pages: tuple[PageInfo, ...]
    outputs: tuple[Path, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_version": self.spec.version,
            "inputs": [i.to_dict() for i in self.inputs],
            "pages": [p.to_dict() for p in self.pages],
            # Named explicitly: ``inputs[].page_count`` is the *source* page count,
            # so a bare top-level ``page_count`` reads ambiguously in JSON output.
            "output_page_count": self.page_count,
            "outputs": [str(o) for o in self.outputs],
            "deterministic": self.spec.deterministic,
            "metadata": dict(self.spec.metadata),
            "encrypted_output": self.spec.output_password is not None,
            "warnings": list(self.warnings),
        }


def _open_input(path: Path, password: str | None, input_id: str):
    """Open one input for inspection, mapping pikepdf failures onto our codes."""
    import pikepdf

    if not path.exists():
        raise ApiError(
            ErrorCode.INPUT_NOT_FOUND,
            f"input {input_id!r} does not exist: {path}",
            details={"input": input_id, "path": str(path)},
        )
    if not path.is_file():
        raise ApiError(
            ErrorCode.INPUT_NOT_FOUND,
            f"input {input_id!r} is not a regular file: {path}",
            details={"input": input_id, "path": str(path)},
        )
    try:
        return pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError:
        # Distinguish "locked, no password given" from "wrong password": the
        # first is a usage error the caller can fix, the second means the
        # credential is stale.
        if not password:
            raise ApiError(
                ErrorCode.INPUT_ENCRYPTED,
                f"input {input_id!r} is encrypted and no password was supplied",
                details={"input": input_id, "path": str(path)},
                hint="Add \"password\" to the inputs[] entry for this file.",
            ) from None
        raise ApiError(
            ErrorCode.INPUT_PASSWORD_WRONG,
            f"input {input_id!r} rejected the supplied password",
            details={"input": input_id, "path": str(path)},
        ) from None
    except Exception as exc:
        raise ApiError(
            ErrorCode.INPUT_NOT_PDF,
            f"input {input_id!r} is not a readable PDF: {type(exc).__name__}",
            details={"input": input_id, "path": str(path), "reason": str(exc)},
        ) from exc


def _measure(page_obj: Any, exporter: Any, sides_cls: Any, crop: Sequence[float],
             scale: float, rotate: int) -> tuple[float, float]:
    """Return the output page size in points, matching the engine's arithmetic."""
    try:
        box = exporter._mediabox(page_obj, sides_cls(*crop))
        w = abs(float(box[2]) - float(box[0]))
        h = abs(float(box[3]) - float(box[1]))
    except Exception:  # pragma: no cover - defensive: fall back to MediaBox
        box = page_obj.mediabox
        w = abs(float(box[2]) - float(box[0]))
        h = abs(float(box[3]) - float(box[1]))
    w *= scale
    h *= scale
    if rotate % 180 == 90:
        w, h = h, w
    return w, h


def plan(spec: Spec, *, max_page_points: float = MAX_PAGE_POINTS) -> Plan:
    """Resolve ``spec`` against the filesystem and return a :class:`Plan`.

    Raises :class:`ApiError` for anything that would make the output wrong.
    """
    from ._engine import load_engine

    engine = load_engine()
    exporter = engine["exporter"]
    sides_cls = engine["Sides"]

    infos: list[InputInfo] = []
    opened: dict[str, Any] = {}
    try:
        for ref in spec.inputs:
            doc = _open_input(ref.path, ref.password, ref.id)
            opened[ref.id] = doc
            infos.append(
                InputInfo(
                    id=ref.id,
                    path=ref.path,
                    page_count=len(doc.pages),
                    encrypted=bool(doc.is_encrypted),
                    pdf_version=str(doc.pdf_version),
                    size_bytes=os.path.getsize(ref.path),
                )
            )

        counts = {i.id: i.page_count for i in infos}

        # Expand "N-" ranges now that the page counts are known.  Done before
        # any validation so downstream code only ever sees concrete pages.
        spec = _expand_open_ranges(spec, counts)

        # Validate every referenced page index before doing any arithmetic, so
        # the error names the spec entry rather than qpdf's "parsing numeric
        # range" message, which points at the input file instead.
        for ps in spec.pages:
            if ps.is_blank:
                continue
            assert ps.input_id is not None and ps.page is not None
            n = counts[ps.input_id]
            if not 1 <= ps.page <= n:
                ref_path = str(next(i.path for i in infos if i.id == ps.input_id))
                raise page_out_of_range(ref_path, ps.page, n)
            for layer in (ps.underlay, ps.overlay):
                if layer is None:
                    continue
                ln = counts[layer.input_id]
                if not 1 <= layer.page <= ln:
                    ref_path = str(next(i.path for i in infos if i.id == layer.input_id))
                    raise page_out_of_range(ref_path, layer.page, ln)

        pages: list[PageInfo] = []
        warnings: list[str] = []
        for ps in spec.pages:
            if ps.is_blank:
                w, h = float(ps.width or 0), float(ps.height or 0)
                if ps.rotate % 180 == 90:
                    w, h = h, w
                w, h = w * ps.scale, h * ps.scale
                pages.append(
                    PageInfo(
                        index=ps.index,
                        source="blank",
                        width=w,
                        height=h,
                        rotate=ps.rotate,
                        scale=ps.scale,
                        crop=ps.crop.as_tuple(),
                        blank=True,
                    )
                )
            else:
                assert ps.input_id is not None and ps.page is not None
                doc = opened[ps.input_id]
                page_obj = doc.pages[ps.page - 1]
                w, h = _measure(
                    page_obj, exporter, sides_cls, ps.crop.as_tuple(), ps.scale, ps.rotate
                )
                pages.append(
                    PageInfo(
                        index=ps.index,
                        source=f"{ps.input_id}:{ps.page}",
                        width=w,
                        height=h,
                        rotate=ps.rotate,
                        scale=ps.scale,
                        crop=ps.crop.as_tuple(),
                    )
                )

        for info in pages:
            if info.width > max_page_points or info.height > max_page_points:
                warnings.append(
                    f"page {info.index} is {info.width:.0f}x{info.height:.0f} pt, "
                    f"above the {max_page_points:.0f} pt recommendation"
                )
            if min(info.width, info.height) < 1:
                warnings.append(
                    f"page {info.index} is smaller than 1 pt on one side; "
                    "it will be effectively invisible"
                )

        outputs = _resolve_outputs(spec)
        _check_outputs(spec, outputs, [i.path for i in infos])
    finally:
        for doc in opened.values():
            try:
                doc.close()
            except Exception:  # pragma: no cover
                pass

    return Plan(
        spec=spec,
        inputs=tuple(infos),
        pages=tuple(pages),
        outputs=tuple(outputs),
        warnings=tuple(warnings),
    )


def _expand_open_ranges(spec: Spec, counts: dict[str, int]) -> Spec:
    """Turn ``"3-"`` entries into concrete page lists using real page counts."""
    if not any(p.open_end for p in spec.pages):
        return spec

    expanded: list[PageSpec] = []
    for ps in spec.pages:
        if not ps.open_end or ps.is_blank or ps.input_id is None or ps.page is None:
            expanded.append(ps)
            continue
        total = counts[ps.input_id]
        for pno in range(ps.page, total + 1):
            expanded.append(
                PageSpec(
                    index=len(expanded) + 1,
                    input_id=ps.input_id,
                    page=pno,
                    rotate=ps.rotate,
                    scale=ps.scale,
                    crop=ps.crop,
                    underlay=ps.underlay,
                    overlay=ps.overlay,
                )
            )

    # Re-number so every page has a dense, 1-based index again.
    renumbered = [
        PageSpec(
            index=i,
            input_id=p.input_id,
            page=p.page,
            rotate=p.rotate,
            scale=p.scale,
            crop=p.crop,
            blank=p.blank,
            width=p.width,
            height=p.height,
            underlay=p.underlay,
            overlay=p.overlay,
        )
        for i, p in enumerate(expanded, start=1)
    ]
    return Spec(
        version=spec.version,
        inputs=spec.inputs,
        pages=tuple(renumbered),
        output=spec.output,
        metadata=spec.metadata,
        deterministic=spec.deterministic,
        output_password=spec.output_password,
        split=spec.split,
    )


def _resolve_outputs(spec: Spec) -> list[Path]:
    """One path normally, one per page when ``split`` is set."""
    if spec.split != "page":
        return [spec.output]
    n = len(spec.pages)
    from .spec import MAX_SPLIT_OUTPUTS

    if n > MAX_SPLIT_OUTPUTS:
        raise ApiError(
            ErrorCode.SPLIT_TOO_MANY_OUTPUTS,
            f"splitting would create {n} files, above the limit of {MAX_SPLIT_OUTPUTS}",
            details={"pages": n, "limit": MAX_SPLIT_OUTPUTS},
            hint="Split into batches, or drop the 'split' option.",
        )
    width = max(3, len(str(n)))
    stem, suffix = spec.output.stem, spec.output.suffix or ".pdf"
    parent = spec.output.parent
    return [parent / f"{stem}-{i:0{width}d}{suffix}" for i in range(1, n + 1)]


def _check_outputs(spec: Spec, outputs: Sequence[Path], inputs: Sequence[Path]) -> None:
    """Refuse to clobber an input, and require a writable parent directory."""
    resolved_inputs = {p.resolve() for p in inputs}
    for out in outputs:
        if out.resolve() in resolved_inputs:
            raise ApiError(
                ErrorCode.OUTPUT_SAME_AS_INPUT,
                f"output would overwrite an input: {out}",
                details={"path": str(out)},
                hint="Choose a different output path.",
            )
        parent = out.parent
        if not parent.exists():
            raise ApiError(
                ErrorCode.OUTPUT_NOT_WRITABLE,
                f"output directory does not exist: {parent}",
                details={"path": str(out), "directory": str(parent)},
                hint="Create the directory first, or point --output elsewhere.",
            )
        if not os.access(parent, os.W_OK):
            raise ApiError(
                ErrorCode.OUTPUT_NOT_WRITABLE,
                f"output directory is not writable: {parent}",
                details={"path": str(out), "directory": str(parent)},
            )
