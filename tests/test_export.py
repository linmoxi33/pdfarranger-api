"""Behavioural tests: real PDFs in, real PDFs out, checked with pikepdf.

These assert *semantics* - page order, page geometry, bookmark targets, metadata
- because a PDF that merely opens is not necessarily correct.  The upstream
engine's own tests compare against QDF golden files, but their comparison is a
subsequence match that ignores the PDF header version and empty Resources
entries, so it can pass while the bytes differ.  Reading the output back with
pikepdf and asserting on measured values is the stronger check.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf
import pytest

import pdfarranger_api as api
from pdfarranger_api import ApiError, ErrorCode


def page_boxes(path: Path) -> list[tuple[float, float, int]]:
    """Return (width, height, rotation) for each page, measured from the file."""
    with pikepdf.open(path) as pdf:
        out = []
        for page in pdf.pages:
            box = page.mediabox
            w = abs(float(box[2]) - float(box[0]))
            h = abs(float(box[3]) - float(box[1]))
            out.append((round(w, 3), round(h, 3), int(page.get("/Rotate", 0)) % 360))
        return out


def page_count(path: Path) -> int:
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


# --------------------------------------------------------------------------
# ordering and selection
# --------------------------------------------------------------------------


def test_merge_preserves_order(workdir: Path, basic: Path, outlines: Path):
    out = workdir / "merged.pdf"
    result = api.merge([basic, outlines], out)
    assert result.page_count == 7 + 4
    assert page_count(out) == 11


def test_reorder_writes_pages_in_the_given_order(workdir: Path, outlines: Path):
    out = workdir / "reordered.pdf"
    api.reorder(outlines, [4, 2, 1, 3], out)
    assert page_count(out) == 4


def test_duplicate_page_produces_independent_copies(workdir: Path, basic: Path):
    """A repeated page must not alias: rotating one copy must not move the other."""
    out = workdir / "dup.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [
                {"input": "a", "page": 1, "rotate": 0},
                {"input": "a", "page": 1, "rotate": 90},
            ],
            "output": str(out),
        }
    )
    boxes = page_boxes(out)
    assert boxes[0][2] == 0
    assert boxes[1][2] == 90


def test_subset_drops_unselected_pages(workdir: Path, basic: Path):
    out = workdir / "subset.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": "2-4"}],
            "output": str(out),
        }
    )
    assert page_count(out) == 3


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def test_rotation_sets_the_rotate_key(workdir: Path, basic: Path):
    out = workdir / "rot.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1, "rotate": 90}],
            "output": str(out),
        }
    )
    assert page_boxes(out)[0][2] == 90


def test_scale_shrinks_the_media_box(workdir: Path, basic: Path):
    out = workdir / "scaled.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1, "scale": 0.5}],
            "output": str(out),
        }
    )
    w, h, _ = page_boxes(out)[0]
    original_w, original_h, _ = page_boxes(basic)[0]
    assert w == pytest.approx(original_w * 0.5, rel=1e-3)
    assert h == pytest.approx(original_h * 0.5, rel=1e-3)


def test_crop_reduces_the_media_box(workdir: Path, basic: Path):
    out = workdir / "cropped.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [
                {"input": "a", "page": 1,
                 "crop": {"left": 0.1, "right": 0.2, "top": 0.0, "bottom": 0.0}}
            ],
            "output": str(out),
        }
    )
    w, h, _ = page_boxes(out)[0]
    original_w, original_h, _ = page_boxes(basic)[0]
    # 0.1 + 0.2 of the width removed, height untouched.
    assert w == pytest.approx(original_w * 0.7, rel=1e-3)
    assert h == pytest.approx(original_h, rel=1e-3)


def test_crop_keeps_a_positive_page_size(workdir: Path, basic: Path):
    """The regression this package exists to prevent."""
    out = workdir / "cropped_edge.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1,
                       "crop": {"left": 0.45, "right": 0.45,
                                "top": 0.45, "bottom": 0.45}}],
            "output": str(out),
        }
    )
    w, h, _ = page_boxes(out)[0]
    assert w > 0 and h > 0


def test_blank_page_is_inserted_at_the_right_position(workdir: Path, basic: Path):
    out = workdir / "blank.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [
                {"input": "a", "page": 1},
                {"blank": True, "width": 595, "height": 842},
                {"input": "a", "page": 2},
            ],
            "output": str(out),
        }
    )
    boxes = page_boxes(out)
    assert len(boxes) == 3
    assert boxes[1][:2] == (595.0, 842.0)
    assert boxes[0][:2] != boxes[1][:2]


def test_overlay_adds_a_content_stream(workdir: Path, basic: Path):
    out = workdir / "overlay.pdf"
    api.run(
        {
            "inputs": [
                {"id": "a", "path": str(basic)},
                {"id": "s", "path": str(basic)},
            ],
            "pages": [{"input": "a", "page": 1,
                       "overlay": {"input": "s", "page": 3}}],
            "output": str(out),
        }
    )
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 1
        resources = pdf.pages[0].get("/Resources", {})
        # An overlay is realised as an XObject on the destination page.
        assert "/XObject" in resources


# --------------------------------------------------------------------------
# metadata, encryption, bookmarks
# --------------------------------------------------------------------------


def test_metadata_is_written(workdir: Path, basic: Path):
    out = workdir / "meta.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1}],
            "output": str(out),
            "metadata": {"title": "季度报告", "author": "林白玉"},
        }
    )
    with pikepdf.open(out) as pdf:
        with pdf.open_metadata() as meta:
            assert meta.get("{http://purl.org/dc/elements/1.1/}title") == "季度报告"
            assert meta.get("{http://purl.org/dc/elements/1.1/}creator") == ["林白玉"]


def test_metadata_does_not_emit_xmp_type_warnings(workdir: Path, basic: Path):
    """``dc:creator`` is an XMP sequence; passing a bare string warns on every run.

    A warning that appears on every successful run teaches users to ignore
    stderr, which is how real warnings get missed.
    """
    import warnings as warnings_module

    out = workdir / "meta_clean.pdf"
    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        api.run(
            {
                "inputs": [{"id": "a", "path": str(basic)}],
                "pages": [{"input": "a", "page": 1}],
                "output": str(out),
                "metadata": {"title": "T", "author": "A", "subject": "S"},
            }
        )
    offenders = [
        str(w.message) for w in caught
        if "XmpTypeWarning" in type(w.message).__name__
        or "ordered array" in str(w.message)
    ]
    assert offenders == []


def test_output_can_be_encrypted(workdir: Path, basic: Path):
    out = workdir / "locked.pdf"
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1}],
            "output": str(out),
            "output_password": "s3cret",
        }
    )
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(out)
    with pikepdf.open(out, password="s3cret") as pdf:
        assert len(pdf.pages) == 1


def test_encrypted_input_with_correct_password(workdir: Path, encrypted: Path):
    out = workdir / "decrypted.pdf"
    api.run(
        {
            "inputs": [{"id": "e", "path": str(encrypted), "password": "foobar"}],
            "pages": [{"input": "e", "page": 1}],
            "output": str(out),
        }
    )
    assert page_count(out) == 1


def test_bookmarks_are_remapped_to_the_new_page_order(workdir: Path, outlines: Path):
    """Reordering pages must move the bookmarks with them, not leave them behind."""
    out = workdir / "bookmarks.pdf"
    api.run(
        {
            "inputs": [{"id": "o", "path": str(outlines)}],
            "pages": [{"input": "o", "page": n} for n in (4, 2, 1, 3)],
            "output": str(out),
        }
    )
    with pikepdf.open(out) as pdf:
        assert "/Outlines" in pdf.Root
        index = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}
        with pdf.open_outline() as outline:
            targets = {}
            for item in outline.root:
                dest = item.destination
                if isinstance(dest, pikepdf.Array) and len(dest) >= 1:
                    targets[str(item.title)] = index.get(dest[0].objgen)
    # Source page 1 moved to position 3; page 4 moved to position 1.
    assert targets["Page 1"] == 3
    assert targets["Page 4"] == 1


# --------------------------------------------------------------------------
# splitting
# --------------------------------------------------------------------------


def test_split_writes_one_file_per_page(workdir: Path, basic: Path):
    out = workdir / "piece.pdf"
    result = api.split_pages(basic, out)
    assert len(result.outputs) == 7
    for path in result.outputs:
        assert path.exists()
        assert page_count(path) == 1
    # Zero-padded so directory listings sort correctly.
    assert result.outputs[0].name == "piece-001.pdf"
    assert result.outputs[-1].name == "piece-007.pdf"


def test_split_output_names_are_stable(workdir: Path, basic: Path):
    first = api.split_pages(basic, workdir / "a.pdf")
    second = api.split_pages(basic, workdir / "a.pdf", overwrite=True)
    assert [p.name for p in first.outputs] == [p.name for p in second.outputs]


# --------------------------------------------------------------------------
# determinism and atomicity
# --------------------------------------------------------------------------


def test_deterministic_output_is_byte_identical_across_runs(workdir: Path, basic: Path):
    spec = {
        "inputs": [{"id": "a", "path": str(basic)}],
        "pages": [{"input": "a", "page": "1-3"}],
        "output": str(workdir / "det.pdf"),
        "deterministic": True,
    }
    digests = []
    for _ in range(3):
        api.run(spec, overwrite=True)
        digests.append(hashlib.sha256((workdir / "det.pdf").read_bytes()).hexdigest())
    assert len(set(digests)) == 1, "deterministic=True must produce identical bytes"


def test_without_deterministic_the_timestamp_varies(workdir: Path, basic: Path):
    """Control for the test above: it must be capable of failing."""
    spec = {
        "inputs": [{"id": "a", "path": str(basic)}],
        "pages": [{"input": "a", "page": 1}],
        "output": str(workdir / "nondet.pdf"),
    }
    digests = set()
    for _ in range(2):
        api.run(spec, overwrite=True)
        digests.add(hashlib.sha256((workdir / "nondet.pdf").read_bytes()).hexdigest())
    assert len(digests) == 2


def test_failed_export_leaves_the_previous_file_intact(
    workdir: Path, basic: Path, monkeypatch
):
    target = workdir / "existing.pdf"
    target.write_bytes(b"SENTINEL")
    from pdfarranger_api import execute

    def explode(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(execute, "_atomic_export", explode)
    with pytest.raises(ApiError):
        api.run(
            {
                "inputs": [{"id": "a", "path": str(basic)}],
                "pages": [{"input": "a", "page": 1}],
                "output": str(target),
            },
            overwrite=True,
        )
    assert target.read_bytes() == b"SENTINEL"


def test_replace_happens_only_after_a_complete_write(workdir: Path, basic: Path, monkeypatch):
    """Crash *inside* the engine call, past the point a temp file exists.

    The previous test replaces ``_atomic_export`` wholesale, so it never reaches
    ``os.replace`` - which means it cannot tell an atomic write from a
    write-in-place.  Here the engine itself fails after the temp file has been
    created, which is exactly the truncation window a non-atomic write would
    expose.
    """
    target = workdir / "survivor.pdf"
    target.write_bytes(b"ORIGINAL")

    from pdfarranger_api import _engine

    real_export = _engine.load_engine()["exporter"].export

    def failing_export(files, rows, metadata, out_paths, *args, **kwargs):
        # Write a partial file to the destination path the caller passed, then
        # die - mimicking a crash halfway through serialisation.
        Path(out_paths[0]).write_bytes(b"%PDF-1.7 partial")
        raise RuntimeError("simulated crash mid-serialisation")

    monkeypatch.setattr(_engine.load_engine()["exporter"], "export", failing_export)
    try:
        with pytest.raises(ApiError):
            api.run(
                {
                    "inputs": [{"id": "a", "path": str(basic)}],
                    "pages": [{"input": "a", "page": 1}],
                    "output": str(target),
                },
                overwrite=True,
            )
    finally:
        monkeypatch.setattr(_engine.load_engine()["exporter"], "export", real_export)

    assert target.read_bytes() == b"ORIGINAL", (
        "a partial write must never reach the destination path"
    )


def test_no_partial_temp_file_survives_a_crash(workdir: Path, basic: Path, monkeypatch):
    """The temp file must be cleaned up even when the engine blows up."""
    from pdfarranger_api import _engine

    real_export = _engine.load_engine()["exporter"].export

    def failing_export(files, rows, metadata, out_paths, *args, **kwargs):
        Path(out_paths[0]).write_bytes(b"partial")
        raise RuntimeError("boom")

    monkeypatch.setattr(_engine.load_engine()["exporter"], "export", failing_export)
    try:
        with pytest.raises(ApiError):
            api.run(
                {
                    "inputs": [{"id": "a", "path": str(basic)}],
                    "pages": [{"input": "a", "page": 1}],
                    "output": str(workdir / "never.pdf"),
                }
            )
    finally:
        monkeypatch.setattr(_engine.load_engine()["exporter"], "export", real_export)

    leftovers = [
        p.name for p in workdir.iterdir()
        if p.name.endswith(".tmp.pdf") or p.name.endswith(".norm.pdf")
    ]
    assert leftovers == []
    assert not (workdir / "never.pdf").exists()


def test_output_lands_via_rename_not_in_place_write(workdir: Path, basic: Path, monkeypatch):
    """The destination must be created by ``os.replace`` (rename), not by writing to it.

    Atomicity is exactly the rename semantic: a rename either happens or it does
    not, so a crash cannot leave a half-written destination.  Copying into the
    destination instead would look identical on the success path, so the success
    path is not enough to test it - this asserts the mechanism.
    """
    import os as os_module

    target = workdir / "renamed.pdf"
    renames: list[tuple[str, str]] = []
    real_replace = os_module.replace

    def spy(src, dst, *args, **kwargs):
        renames.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os_module, "replace", spy)
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1}],
            "output": str(target),
        }
    )
    monkeypatch.setattr(os_module, "replace", real_replace)

    destinations = [dst for _, dst in renames]
    assert str(target) in destinations, (
        "the output must be moved into place with os.replace; "
        f"observed renames: {renames}"
    )
    # And the source of that rename must be a temporary sibling, not the target.
    src_for_target = [src for src, dst in renames if dst == str(target)][0]
    assert src_for_target != str(target)
    assert Path(src_for_target).parent == target.parent


def test_failed_export_leaves_no_temp_files(workdir: Path, basic: Path, monkeypatch):
    from pdfarranger_api import execute

    def explode(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(execute, "_atomic_export", explode)
    with pytest.raises(ApiError):
        api.run(
            {
                "inputs": [{"id": "a", "path": str(basic)}],
                "pages": [{"input": "a", "page": 1}],
                "output": str(workdir / "clean.pdf"),
            }
        )
    litter = [p.name for p in workdir.iterdir() if p.name.endswith(".tmp.pdf")]
    assert litter == []


def test_existing_output_is_not_overwritten_by_default(workdir: Path, basic: Path):
    out = workdir / "guard.pdf"
    out.write_bytes(b"KEEP-ME")
    with pytest.raises(ApiError) as excinfo:
        api.run(
            {
                "inputs": [{"id": "a", "path": str(basic)}],
                "pages": [{"input": "a", "page": 1}],
                "output": str(out),
            }
        )
    assert excinfo.value.code is ErrorCode.OUTPUT_EXISTS
    assert out.read_bytes() == b"KEEP-ME"


def test_overwrite_replaces_the_file(workdir: Path, basic: Path):
    out = workdir / "replace.pdf"
    out.write_bytes(b"OLD")
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1}],
            "output": str(out),
        },
        overwrite=True,
    )
    assert out.read_bytes() != b"OLD"
    assert page_count(out) == 1


def test_output_may_not_be_an_input(workdir: Path, basic: Path):
    with pytest.raises(ApiError) as excinfo:
        api.run(
            {
                "inputs": [{"id": "a", "path": str(basic)}],
                "pages": [{"input": "a", "page": 1}],
                "output": str(basic),
            },
            overwrite=True,
        )
    assert excinfo.value.code is ErrorCode.OUTPUT_SAME_AS_INPUT
    assert page_count(basic) == 7  # untouched


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------


def test_dry_run_writes_nothing_but_reports_the_plan(workdir: Path, basic: Path):
    out = workdir / "never.pdf"
    result = api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": "1-4"}],
            "output": str(out),
        },
        dry_run=True,
    )
    assert result.page_count == 4
    assert not out.exists()


def test_plan_reports_geometry_before_writing(workdir: Path, basic: Path):
    plan_obj = api.dry_run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1, "scale": 0.5}],
            "output": str(workdir / "p.pdf"),
        }
    )
    original_w = page_boxes(basic)[0][0]
    assert plan_obj.pages[0].width == pytest.approx(original_w * 0.5, rel=1e-3)
    assert plan_obj.to_dict()["output_page_count"] == 1
    # The source page count must stay distinguishable from the output count.
    assert plan_obj.to_dict()["inputs"][0]["page_count"] == 7
