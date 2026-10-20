"""Equivalence with the upstream engine.

The claim this package makes is "same engine, better interface" - not "a
reimplementation".  These tests hold that claim to account by driving upstream's
``exporter.export()`` directly with hand-built ``Page`` objects and comparing the
result to what the API produces for the same logical operation.

Comparison is *semantic* (page count, MediaBox, /Rotate, bookmark targets), not
byte equality: upstream stamps a fresh XMP timestamp on every save, and the qpdf
version it writes depends on the input version, so byte comparison would fail
for reasons unrelated to correctness.  Where byte equality *is* meaningful -
deterministic mode - it is asserted separately in ``test_export.py``.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

import pdfarranger_api as api
from pdfarranger_api._engine import load_engine


def upstream_export(
    sources: list[Path],
    page_rows: list[tuple[int, int]],
    destination: Path,
    *,
    rotations: dict[int, int] | None = None,
    scales: dict[int, float] | None = None,
) -> None:
    """Call upstream ``export()`` directly, bypassing this package's API.

    ``page_rows`` is a list of ``(file_index, page_number)`` both 1-based, which
    is exactly how upstream addresses pages.
    """
    engine = load_engine()
    Page = engine["Page"]
    Sides = engine["Sides"]
    Dims = engine["Dims"]
    export = engine["exporter"].export

    rotations = rotations or {}
    scales = scales or {}

    rows = []
    for pos, (nfile, npage) in enumerate(page_rows):
        rows.append(
            Page(
                nfile,
                npage,
                1.0,
                str(sources[nfile - 1]),
                rotations.get(pos, 0),
                scales.get(pos, 1.0),
                Sides(),
                Sides(),
                Dims(0, 0),
                f"p{npage}",
                [],
            )
        )

    class _Cfg:
        def start_with_empty(self) -> bool:
            return False

    export(
        [(str(p), "") for p in sources],
        rows,
        {},
        [str(destination)],
        _Cfg(),
        None,
        False,
    )


def signature(path: Path) -> list[tuple[float, float, int]]:
    """Measured page geometry, comparable across producers."""
    with pikepdf.open(path) as pdf:
        out = []
        for page in pdf.pages:
            box = page.mediabox
            w = abs(float(box[2]) - float(box[0]))
            h = abs(float(box[3]) - float(box[1]))
            out.append((round(w, 2), round(h, 2), int(page.get("/Rotate", 0)) % 360))
        return out


def test_plain_reorder_matches_upstream(workdir: Path, basic: Path):
    """Same page order through both paths must yield the same geometry."""
    upstream_out = workdir / "upstream.pdf"
    api_out = workdir / "api.pdf"

    upstream_export([basic], [(1, 3), (1, 1), (1, 5)], upstream_out)
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": [3, 1, 5]}],
            "output": str(api_out),
        }
    )

    assert signature(api_out) == signature(upstream_out)


def test_rotation_matches_upstream(workdir: Path, basic: Path):
    upstream_out = workdir / "up_rot.pdf"
    api_out = workdir / "api_rot.pdf"

    upstream_export(
        [basic], [(1, 1), (1, 2)], upstream_out, rotations={0: 90, 1: 180}
    )
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [
                {"input": "a", "page": 1, "rotate": 90},
                {"input": "a", "page": 2, "rotate": 180},
            ],
            "output": str(api_out),
        }
    )

    assert signature(api_out) == signature(upstream_out)


def test_scale_matches_upstream(workdir: Path, basic: Path):
    upstream_out = workdir / "up_scale.pdf"
    api_out = workdir / "api_scale.pdf"

    upstream_export([basic], [(1, 1)], upstream_out, scales={0: 0.25})
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": 1, "scale": 0.25}],
            "output": str(api_out),
        }
    )

    assert signature(api_out) == signature(upstream_out)


def test_multi_file_merge_matches_upstream(workdir: Path, basic: Path, outlines: Path):
    upstream_out = workdir / "up_merge.pdf"
    api_out = workdir / "api_merge.pdf"

    upstream_export([basic, outlines], [(1, 1), (2, 1), (1, 2)], upstream_out)
    api.run(
        {
            "inputs": [
                {"id": "a", "path": str(basic)},
                {"id": "b", "path": str(outlines)},
            ],
            "pages": [
                {"input": "a", "page": 1},
                {"input": "b", "page": 1},
                {"input": "a", "page": 2},
            ],
            "output": str(api_out),
        }
    )

    assert signature(api_out) == signature(upstream_out)


def test_upstream_writes_a_broken_page_for_overflowing_crop(workdir: Path, basic: Path):
    """Document the upstream behaviour this package refuses to reproduce.

    If upstream ever starts raising instead, this test fails and the guard in
    ``spec.py`` can be revisited.
    """
    engine = load_engine()
    Page = engine["Page"]
    Sides = engine["Sides"]
    Dims = engine["Dims"]

    row = Page(
        1, 1, 1.0, str(basic), 0, 1.0,
        Sides(0.6, 0.6, 0.0, 0.0),  # sums to 1.2 -> negative width
        Sides(), Dims(0, 0), "p1", [],
    )

    class _Cfg:
        def start_with_empty(self) -> bool:
            return False

    out = workdir / "upstream_broken.pdf"
    engine["exporter"].export(
        [(str(basic), "")], [row], {}, [str(out)], _Cfg(), None, False
    )

    with pikepdf.open(out) as pdf:
        box = pdf.pages[0].mediabox
        width = float(box[2]) - float(box[0])
    # Upstream silently produced a page whose right edge is left of its left.
    assert width < 0, "upstream behaviour changed; revisit the CROP_OUT_OF_RANGE guard"


def test_api_refuses_the_same_input_that_breaks_upstream(workdir: Path, basic: Path):
    """The paired negative test: what upstream corrupts, the API rejects."""
    with pytest.raises(api.ApiError) as excinfo:
        api.run(
            {
                "inputs": [{"id": "a", "path": str(basic)}],
                "pages": [{"input": "a", "page": 1,
                           "crop": {"left": 0.6, "right": 0.6}}],
                "output": str(workdir / "should_not_exist.pdf"),
            }
        )
    assert excinfo.value.code is api.ErrorCode.CROP_OUT_OF_RANGE
    assert not (workdir / "should_not_exist.pdf").exists()


def test_api_does_not_modify_inputs(workdir: Path, basic: Path):
    """A batch job must be safe to re-run: inputs are read, never written."""
    before = basic.read_bytes()
    api.run(
        {
            "inputs": [{"id": "a", "path": str(basic)}],
            "pages": [{"input": "a", "page": "1-3"}],
            "output": str(workdir / "copy.pdf"),
        }
    )
    assert basic.read_bytes() == before


def test_upstream_golden_suite_still_passes():
    """Run upstream's own exporter tests against the vendored copy.

    This is the load-bearing check that the vendored engine is unmodified: if the
    four files differed from upstream in any way that affects output, these 29
    golden-file comparisons would fail.
    """
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(repo / "tools" / "run_upstream_suite.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(repo),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "29" in result.stdout or "OK" in result.stdout
