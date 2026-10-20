"""Execution: turn a :class:`~pdfarranger_api.plan.Plan` into PDF files.

The engine call itself is upstream's ``exporter.export()``.  Everything in this
module is about the things a GUI never has to care about but a program does:

Atomic writes
    Upstream writes straight to the destination.  A crash mid-write (or a killed
    CI job) leaves a truncated PDF that looks like a valid file to the next
    step.  Every output here is written to a sibling temp file and then
    ``os.replace``-d into place, so the destination is either the old file or
    the complete new one.

Deterministic output
    Two runs of the same spec produce byte-identical files when
    ``deterministic`` is set.  Upstream stamps ``xmp:MetadataDate`` with the
    current time on every save, so the bytes differ run to run; the stamp is
    rewritten and the file re-saved with ``static_id``/``deterministic_id``.

No silent success
    Upstream reports some failures by printing a traceback to stderr and
    returning normally, leaving no file behind.  Here a missing output file after
    a "successful" call is an error.

Blank pages
    Upstream creates blank pages through ``get_blank_doc``, which needs the
    GUI's document queue.  A blank page is just a page with no content stream,
    so we synthesise one PDF per distinct blank size and register it as an extra
    entry in the engine's file list.  The engine addresses pages as
    ``files[nfile - 1].pages[npage - 1]``, so the synthetic document has to be a
    real list entry - not a side channel.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .errors import ApiError, ErrorCode
from .plan import Plan
from .spec import META_XMP, PageSpec, Spec

__all__ = ["ApplyResult", "apply"]

_METADATA_DATE = "{http://ns.adobe.com/xap/1.0/}MetadataDate"
_FIXED_STAMP = "2000-01-01T00:00:00+00:00"
_STAMP_RE = re.compile(rb"(<xmp:MetadataDate[^>]*>)[^<]*(</xmp:MetadataDate>)")


@dataclass(frozen=True)
class ApplyResult:
    """What was written."""

    outputs: tuple[Path, ...]
    page_count: int
    bytes_written: int
    deterministic: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputs": [
                {"path": str(p), "bytes": p.stat().st_size if p.exists() else 0}
                for p in self.outputs
            ],
            "page_count": self.page_count,
            "bytes_written": self.bytes_written,
            "deterministic": self.deterministic,
        }


class _Config:
    """Minimal stand-in for PDF Arranger's app config.

    The engine asks one question of it: whether to start from an empty document
    (``export_doc``) or to use the qpdf Job interface (``export_doc_job``).  The
    Job path is what the GUI uses for normal saves and it preserves bookmarks,
    so that is the one we take.
    """

    def start_with_empty(self) -> bool:  # noqa: D401 - engine callback name
        return False


class _SourceBook:
    """Maps our input ids (and synthetic blank docs) to engine file indices.

    The engine indexes files from 1 and pages from 1, and addresses them as
    ``files[nfile - 1].pages[npage - 1]``.  This class owns that translation so
    the rest of the module can stay in our own vocabulary.
    """

    def __init__(self, spec: Spec, workdir: Path) -> None:
        self._workdir = workdir
        self._files: list[tuple[str, str]] = [
            (str(ref.path), ref.password or "") for ref in spec.inputs
        ]
        self._index: dict[str, int] = {
            ref.id: pos + 1 for pos, ref in enumerate(spec.inputs)
        }
        self._path: dict[str, Path] = {ref.id: ref.path for ref in spec.inputs}
        self._blanks: dict[tuple[float, float], str] = {}

    def nfile_for_input(self, input_id: str) -> int:
        return self._index[input_id]

    def path_for_input(self, input_id: str) -> Path:
        return self._path[input_id]

    def blank_source(self, width: float, height: float) -> int:
        """Register (once) a synthetic single-blank-page PDF and return its index."""
        key = (round(width, 4), round(height, 4))
        if key in self._blanks:
            return self._index[self._blanks[key]]

        import pikepdf

        name = f"__blank_{len(self._blanks) + 1}__"
        path = self._workdir / f"{name}.pdf"
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page(page_size=(width, height))
        pdf.save(path)

        self._files.append((str(path), ""))
        self._index[name] = len(self._files)
        self._blanks[key] = name
        return self._index[name]

    def as_engine_files(self) -> list[tuple[str, str]]:
        return list(self._files)


def _build_rows(spec: Spec, engine: dict[str, Any], book: _SourceBook) -> list[Any]:
    """Translate our PageSpec list into the engine's row objects.

    ``Page`` is upstream's UI model: it also carries thumbnail and zoom state
    that the exporter ignores.  Only ``nfile``/``npage``/``copyname``/``angle``/
    ``scale``/``crop``/``layerpages`` reach the output.
    """
    Page = engine["Page"]
    LayerPage = engine["core"].LayerPage
    Sides = engine["Sides"]
    Dims = engine["Dims"]

    def layer_row(ref: Any, laypos: str) -> Any:
        return LayerPage(
            book.nfile_for_input(ref.input_id),
            ref.page,
            str(book.path_for_input(ref.input_id)),
            0,  # angle
            1.0,  # scale
            Sides(),  # crop
            Sides(),  # offset
            laypos,
            Dims(0, 0),
        )

    rows: list[Any] = []
    for ps in spec.pages:
        layers = []
        if ps.underlay is not None:
            layers.append(layer_row(ps.underlay, "UNDERLAY"))
        if ps.overlay is not None:
            layers.append(layer_row(ps.overlay, "OVERLAY"))

        if ps.is_blank:
            nfile = book.blank_source(float(ps.width or 0), float(ps.height or 0))
            rows.append(
                Page(
                    nfile,
                    1,
                    1.0,
                    "",
                    ps.rotate,
                    ps.scale,
                    Sides(),
                    Sides(),
                    Dims(float(ps.width or 0), float(ps.height or 0)),
                    "blank",
                    layers,
                )
            )
            continue

        assert ps.input_id is not None and ps.page is not None
        rows.append(
            Page(
                book.nfile_for_input(ps.input_id),
                ps.page,
                1.0,  # zoom: thumbnail scale, ignored by the exporter
                str(book.path_for_input(ps.input_id)),  # real path, not a temp copy
                ps.rotate,
                ps.scale,
                Sides(*ps.crop.as_tuple()),
                Sides(),  # hide: a GUI-only preview mask, never exported
                Dims(0, 0),  # size_orig: unused on the export path
                f"{ps.input_id}:{ps.page}",
                layers,
            )
        )
    return rows


def _build_metadata(spec: Spec) -> dict[str, Any]:
    """Translate spec metadata into the XMP predicates the engine writes.

    ``dc:creator`` is an *ordered array* in XMP, so assigning a bare string makes
    pikepdf emit ``XmpTypeWarning: dc:creator holds an ordered array of values
    (rdf:Seq); assign a list or set of strings, not a str``.  The value still
    ends up correct, but a warning on every run trains users to ignore stderr, so
    wrap it as a single-element list here.
    """
    metadata: dict[str, Any] = {}
    for key, value in spec.metadata.items():
        predicate = META_XMP.get(key)
        if predicate is None:
            continue
        if key == "author":
            metadata[predicate] = [value]
        else:
            metadata[predicate] = value
    return metadata


def _atomic_export(
    spec: Spec,
    files: Sequence[tuple[str, str]],
    rows: Sequence[Any],
    destination: Path,
) -> int:
    """Export to a temp file, then move it into place.  Returns bytes written."""
    from ._engine import load_engine

    export = load_engine()["exporter"].export
    metadata = _build_metadata(spec)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp.pdf", dir=str(destination.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        kwargs: dict[str, Any] = {}
        if spec.output_password:
            kwargs["output_password"] = spec.output_password
        export(
            list(files),
            list(rows),
            metadata,
            [str(tmp)],
            _Config(),
            None,  # quit_flag: only meaningful for GUI cancellation
            False,  # test_mode: QDF output is for golden tests, not for users
            **kwargs,
        )

        if not tmp.exists() or tmp.stat().st_size == 0:
            # Upstream reports some failures by printing a traceback and then
            # returning normally; a missing file is the observable symptom.
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                "the export engine returned without writing an output file",
                details={"destination": str(destination)},
                hint="Check the engine traceback printed on stderr above.",
            )

        if spec.deterministic:
            _make_deterministic(tmp)

        size = tmp.stat().st_size
        os.replace(tmp, destination)
        return size
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover
                pass


def _make_deterministic(path: Path) -> None:
    """Normalise the one field that varies between runs: the XMP date stamp."""
    import pikepdf

    normalised = path.with_suffix(".norm.pdf")
    try:
        with pikepdf.open(path) as pdf:
            with pdf.open_metadata() as meta:
                # Setting it inside the block is not enough on its own: pikepdf
                # rewrites MetadataDate when the block exits.  Patch the XMP
                # packet after the fact as well.
                meta[_METADATA_DATE] = _FIXED_STAMP
            if "/Metadata" in pdf.Root:
                raw = bytes(pdf.Root.Metadata.read_bytes())
                patched = _STAMP_RE.sub(
                    b"\\g<1>" + _FIXED_STAMP.encode() + b"\\g<2>", raw
                )
                if patched != raw:
                    pdf.Root.Metadata.write(patched)
            pdf.save(
                normalised, static_id=True, deterministic_id=True, linearize=False
            )
        os.replace(normalised, path)
    finally:
        if normalised.exists():
            try:
                normalised.unlink()
            except OSError:  # pragma: no cover
                pass


def apply(
    plan_obj: Plan,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ApplyResult:
    """Write the outputs described by ``plan_obj``.

    ``overwrite=False`` (the default) refuses to replace an existing file, so a
    re-run of a batch job cannot silently destroy a previous artefact.
    """
    from ._engine import load_engine

    spec = plan_obj.spec
    engine = load_engine()

    if not dry_run:
        for out in plan_obj.outputs:
            if out.exists() and not overwrite:
                raise ApiError(
                    ErrorCode.OUTPUT_EXISTS,
                    f"output already exists: {out}",
                    details={"path": str(out)},
                    hint="Pass --overwrite (CLI) or overwrite=True (SDK) to replace it.",
                )

    workdir = Path(tempfile.mkdtemp(prefix="pdfarranger-api-"))
    written: list[Path] = []
    total = 0
    try:
        book = _SourceBook(spec, workdir)
        rows = _build_rows(spec, engine, book)
        files = book.as_engine_files()

        if dry_run:
            return ApplyResult(
                outputs=plan_obj.outputs,
                page_count=len(rows),
                bytes_written=0,
                deterministic=spec.deterministic,
            )

        if spec.split == "page":
            for row, dest in zip(rows, plan_obj.outputs):
                total += _atomic_export(spec, files, [row], dest)
                written.append(dest)
        else:
            total += _atomic_export(spec, files, rows, plan_obj.outputs[0])
            written.append(plan_obj.outputs[0])
    except ApiError:
        _cleanup(written)
        raise
    except Exception as exc:
        _cleanup(written)
        raise _translate(exc) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return ApplyResult(
        outputs=tuple(written),
        page_count=len(rows),
        bytes_written=total,
        deterministic=spec.deterministic,
    )


def _cleanup(paths: Sequence[Path]) -> None:
    """Remove partial outputs so a failed run leaves nothing behind."""
    for p in paths:
        try:
            if p.exists():
                p.unlink()
        except OSError:  # pragma: no cover
            pass


def _translate(exc: Exception) -> ApiError:
    """Map engine/pikepdf exceptions onto the stable error contract."""
    from ._engine import GuiUnavailable, VendorIntegrityError

    if isinstance(exc, GuiUnavailable):
        return ApiError(
            ErrorCode.GUI_UNAVAILABLE,
            f"a GTK code path was reached in a headless build: {exc}",
            details={"reason": str(exc)},
        )
    if isinstance(exc, VendorIntegrityError):
        return ApiError(
            ErrorCode.ENGINE_INTEGRITY,
            str(exc),
            hint="Reinstall the package; the vendored engine files were modified.",
        )

    name = type(exc).__name__
    try:
        import pikepdf

        if isinstance(exc, pikepdf.PasswordError):
            return ApiError(
                ErrorCode.INPUT_PASSWORD_WRONG,
                "an input rejected its password during export",
                details={"reason": str(exc)},
            )
        if isinstance(exc, pikepdf.PdfError):
            return ApiError(
                ErrorCode.INPUT_NOT_PDF,
                f"the engine rejected an input PDF: {exc}",
                details={"reason": str(exc)},
            )
    except Exception:  # pragma: no cover
        pass

    return ApiError(
        ErrorCode.INTERNAL_ERROR,
        f"unexpected {name} during export: {exc}",
        details={"exception": name, "reason": str(exc)},
    )
