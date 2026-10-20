"""Tests for spec parsing and validation.

Every test here asserts a specific ``ErrorCode``, not just "it raised".  The
codes are the public contract; if one changes, callers' error handling breaks,
so each must be pinned by a test that would fail if the code were renamed or the
check silently dropped.
"""

from __future__ import annotations

import pytest

from pdfarranger_api import ApiError, ErrorCode
from pdfarranger_api.spec import (
    MAX_SCALE,
    SPEC_VERSION,
    Crop,
    parse_page_range,
    parse_spec,
)


def minimal(**overrides):
    base = {
        "inputs": [{"id": "a", "path": "a.pdf"}],
        "pages": [{"input": "a", "page": 1}],
        "output": "out.pdf",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# structural validation
# --------------------------------------------------------------------------


def test_minimal_spec_parses():
    spec = parse_spec(minimal())
    assert spec.version == SPEC_VERSION
    assert len(spec.pages) == 1
    assert spec.pages[0].page == 1
    assert spec.output.name == "out.pdf"


def test_unknown_top_level_field_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(nonsense=1))
    assert excinfo.value.code is ErrorCode.SPEC_UNKNOWN_FIELD
    assert "nonsense" in excinfo.value.details["field"]


def test_unknown_page_field_is_rejected():
    """A typo like ``rotation`` must not be silently ignored."""
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1, "rotation": 90}]))
    assert excinfo.value.code is ErrorCode.SPEC_UNKNOWN_FIELD
    assert excinfo.value.details["field"] == "rotation"


def test_spec_must_be_an_object():
    with pytest.raises(ApiError) as excinfo:
        parse_spec([1, 2, 3])
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_wrong_spec_version_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(version=99))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID
    assert excinfo.value.details["supported"] == [SPEC_VERSION]


def test_empty_pages_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[]))
    assert excinfo.value.code is ErrorCode.NO_PAGES


def test_empty_inputs_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(inputs=[]))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_duplicate_input_ids_are_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(
            minimal(
                inputs=[
                    {"id": "a", "path": "a.pdf"},
                    {"id": "a", "path": "b.pdf"},
                ]
            )
        )
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_page_referencing_unknown_input_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "zzz", "page": 1}]))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID
    assert excinfo.value.details["known"] == ["a"]


def test_missing_output_is_rejected():
    raw = minimal()
    del raw["output"]
    with pytest.raises(ApiError) as excinfo:
        parse_spec(raw)
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


# --------------------------------------------------------------------------
# geometry: the checks that stop upstream from writing a broken page
# --------------------------------------------------------------------------


def test_crop_sides_summing_to_one_is_rejected():
    """Upstream writes a zero-width MediaBox here; we refuse."""
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1,
                                   "crop": {"left": 0.5, "right": 0.5}}]))
    assert excinfo.value.code is ErrorCode.CROP_OUT_OF_RANGE
    assert excinfo.value.details["axis"] == "horizontal"


def test_crop_sides_summing_above_one_is_rejected():
    """Upstream writes a *negative-width* page here, which renders blank."""
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1,
                                   "crop": {"left": 0.6, "right": 0.6}}]))
    assert excinfo.value.code is ErrorCode.CROP_OUT_OF_RANGE
    assert excinfo.value.details["total"] == pytest.approx(1.2)


def test_vertical_crop_overflow_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1,
                                   "crop": {"top": 0.7, "bottom": 0.4}}]))
    assert excinfo.value.code is ErrorCode.CROP_OUT_OF_RANGE
    assert excinfo.value.details["axis"] == "vertical"


def test_negative_crop_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1,
                                   "crop": {"left": -0.1}}]))
    assert excinfo.value.code is ErrorCode.CROP_OUT_OF_RANGE
    assert excinfo.value.details["side"] == "left"


def test_crop_just_under_one_is_accepted():
    spec = parse_spec(minimal(pages=[{"input": "a", "page": 1,
                                      "crop": {"left": 0.49, "right": 0.49}}]))
    assert spec.pages[0].crop.left == pytest.approx(0.49)


def test_zero_scale_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1, "scale": 0}]))
    assert excinfo.value.code is ErrorCode.SCALE_OUT_OF_RANGE


def test_negative_scale_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1, "scale": -2}]))
    assert excinfo.value.code is ErrorCode.SCALE_OUT_OF_RANGE


def test_excessive_scale_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1, "scale": MAX_SCALE + 1}]))
    assert excinfo.value.code is ErrorCode.SCALE_OUT_OF_RANGE
    assert excinfo.value.details["max"] == MAX_SCALE


def test_rotation_must_be_a_multiple_of_90():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1, "rotate": 45}]))
    assert excinfo.value.code is ErrorCode.ANGLE_INVALID


def test_rotation_is_normalised_modulo_360():
    spec = parse_spec(minimal(pages=[{"input": "a", "page": 1, "rotate": 450}]))
    assert spec.pages[0].rotate == 90


def test_negative_rotation_is_normalised():
    spec = parse_spec(minimal(pages=[{"input": "a", "page": 1, "rotate": -90}]))
    assert spec.pages[0].rotate == 270


# --------------------------------------------------------------------------
# page ranges
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,expected",
    [
        (3, [3]),
        ("3", [3]),
        ("1-3", [1, 2, 3]),
        ("3-1", [1, 2, 3]),
        ("1-3,7", [1, 2, 3, 7]),
        ("1;2", [1, 2]),
        ([1, 5], [1, 5]),
        (" 2 - 4 ", [2, 3, 4]),
    ],
)
def test_page_range_expansion(expr, expected):
    pages, open_end = parse_page_range(expr, "pages[0].page")
    assert pages == expected
    assert open_end is False


def test_open_ended_range_is_flagged_not_guessed():
    """``"3-"`` must not guess a page count: the spec parser opens no files."""
    pages, open_end = parse_page_range("3-", "pages[0].page")
    assert pages == [3]
    assert open_end is True


@pytest.mark.parametrize("expr", ["0", "0-3", "abc", "", "1-2-3", 0, -1])
def test_invalid_page_expressions_are_rejected(expr):
    with pytest.raises(ApiError):
        parse_page_range(expr, "pages[0].page")


def test_range_expands_into_one_pagespec_per_page():
    spec = parse_spec(minimal(pages=[{"input": "a", "page": "2-4"}]))
    assert [p.page for p in spec.pages] == [2, 3, 4]
    assert [p.index for p in spec.pages] == [1, 2, 3]


def test_indices_stay_dense_across_entries():
    spec = parse_spec(
        minimal(
            pages=[
                {"input": "a", "page": "1-2"},
                {"input": "a", "page": 5},
                {"input": "a", "page": [7, 8]},
            ]
        )
    )
    assert [p.index for p in spec.pages] == [1, 2, 3, 4, 5]


# --------------------------------------------------------------------------
# blank pages and layers
# --------------------------------------------------------------------------


def test_blank_page_needs_a_size():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"blank": True}]))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_blank_page_size_must_be_positive():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"blank": True, "width": 0, "height": 792}]))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_blank_page_cannot_also_reference_an_input():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(
            minimal(pages=[{"blank": True, "width": 612, "height": 792, "input": "a"}])
        )
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_blank_page_parses_with_size():
    spec = parse_spec(minimal(pages=[{"blank": True, "width": 595, "height": 842}]))
    assert spec.pages[0].is_blank
    assert spec.pages[0].width == 595


def test_layer_must_select_exactly_one_page():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(
            minimal(
                pages=[
                    {"input": "a", "page": 1, "overlay": {"input": "a", "page": "1-3"}}
                ]
            )
        )
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_layer_referencing_unknown_input_is_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(
            minimal(pages=[{"input": "a", "page": 1,
                            "underlay": {"input": "zz", "page": 1}}])
        )
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------


def test_relative_input_paths_resolve_against_the_spec_directory(tmp_path):
    spec = parse_spec(minimal(), base_dir=tmp_path)
    assert spec.inputs[0].path == (tmp_path / "a.pdf").resolve()


def test_metadata_rejects_unknown_keys():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(metadata={"nonsense": "x"}))
    assert excinfo.value.code is ErrorCode.SPEC_UNKNOWN_FIELD


def test_metadata_rejects_non_string_values():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(metadata={"title": 42}))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_deterministic_must_be_boolean():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(deterministic="yes"))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_crop_default_is_zero():
    assert Crop().is_zero()
    assert Crop(0.1, 0.2, 0.3, 0.4).as_tuple() == (0.1, 0.2, 0.3, 0.4)


def test_non_finite_numbers_are_rejected():
    with pytest.raises(ApiError) as excinfo:
        parse_spec(minimal(pages=[{"input": "a", "page": 1, "scale": float("nan")}]))
    assert excinfo.value.code is ErrorCode.SPEC_INVALID


def test_boolean_is_not_accepted_as_a_number():
    """``True`` is an ``int`` in Python; it must not pass as a page number."""
    with pytest.raises(ApiError):
        parse_spec(minimal(pages=[{"input": "a", "page": True}]))
