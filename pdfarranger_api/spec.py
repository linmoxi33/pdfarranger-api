"""The document spec: a declarative description of the output PDF.

A spec is plain JSON (or a Python mapping) so it can be produced by any language,
stored, diffed and replayed::

    {
      "version": 1,
      "inputs": [
        {"id": "a", "path": "report.pdf", "password": null},
        {"id": "b", "path": "appendix.pdf"}
      ],
      "pages": [
        {"input": "a", "page": 1, "rotate": 90},
        {"input": "b", "page": "1-3", "scale": 0.5},
        {"input": "a", "page": 7, "crop": {"left": 0.1, "right": 0.1,
                                           "top": 0.05, "bottom": 0.05}},
        {"blank": true, "width": 612, "height": 792}
      ],
      "output": "merged.pdf",
      "metadata": {"title": "Quarterly report"},
      "deterministic": true
    }

Why a spec rather than CLI flags
--------------------------------
Reordering, cropping, rotating and merging are all *per-page* operations.  A
flag-per-operation CLI cannot express "page 3 of file b, rotated, cropped
asymmetrically, placed between page 1 and page 5 of file a" without inventing an
ad-hoc mini-language on the command line.  The spec *is* that language, it is
validated as a whole before any file is written, and the same document drives
both the CLI and the SDK.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .errors import (
    ApiError,
    ErrorCode,
    angle_invalid,
    crop_out_of_range,
    scale_out_of_range,
    spec_invalid,
    unknown_field,
)

#: Bumped only for breaking changes to the spec format.
SPEC_VERSION = 1

#: Guards against a typo like ``scale: 1e9`` silently producing a petabyte page.
MAX_SCALE = 100.0

#: A 200-inch page at 72 dpi is already absurd for print; refuse beyond it.
MAX_PAGE_POINTS = 200 * 72

#: Upper bound on split output count, so one spec cannot fill a disk.
MAX_SPLIT_OUTPUTS = 512

_ALLOWED_TOP = {"version", "inputs", "pages", "output", "metadata",
                "deterministic", "output_password", "split"}
_ALLOWED_INPUT = {"id", "path", "password"}
_ALLOWED_PAGE = {"input", "page", "rotate", "scale", "crop", "blank",
                 "width", "height", "underlay", "overlay"}
_ALLOWED_CROP = {"left", "right", "top", "bottom"}
_ALLOWED_META = {"title", "subject", "keywords", "author", "creator", "producer"}

#: Metadata keys, mapped to the XMP predicates PDF Arranger writes.
META_XMP = {
    "title": "{http://purl.org/dc/elements/1.1/}title",
    "subject": "{http://purl.org/dc/elements/1.1/}description",
    "keywords": "{http://ns.adobe.com/pdf/1.3/}Keywords",
    "author": "{http://purl.org/dc/elements/1.1/}creator",
    "creator": "{http://ns.adobe.com/xap/1.0/}CreatorTool",
    "producer": "{http://ns.adobe.com/pdf/1.3/}Producer",
}

_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d*)\s*)?$")


def _reject_unknown(obj: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    allowed_set = set(allowed)
    for key in obj:
        if key not in allowed_set:
            raise unknown_field(str(key), where, sorted(allowed_set))


def _as_number(value: Any, where: str, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise spec_invalid(
            f"{where}.{field_name} must be a number, got {type(value).__name__}",
            where=where,
            field=field_name,
            value=repr(value),
        )
    if value != value or value in (float("inf"), float("-inf")):
        raise spec_invalid(
            f"{where}.{field_name} must be finite", where=where, field=field_name
        )
    return float(value)


def _as_str(value: Any, where: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise spec_invalid(
            f"{where}.{field_name} must be a non-empty string",
            where=where,
            field=field_name,
        )
    return value.strip()


@dataclass(frozen=True)
class Crop:
    """Per-side crop as a fraction of the page, 0.0 <= x < 1.0."""

    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.left, self.right, self.top, self.bottom)

    def is_zero(self) -> bool:
        return not any(self.as_tuple())

    @classmethod
    def parse(cls, raw: Any, where: str, page_no: int) -> "Crop":
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise spec_invalid(f"{where}.crop must be an object", where=where)
        _reject_unknown(raw, _ALLOWED_CROP, f"{where}.crop")
        vals = {
            name: _as_number(raw.get(name, 0.0), f"{where}.crop", name)
            for name in _ALLOWED_CROP
        }
        for name, v in vals.items():
            if v < 0:
                raise ApiError(
                    ErrorCode.CROP_OUT_OF_RANGE,
                    f"crop.{name} on page {page_no} is negative ({v})",
                    details={"page": page_no, "side": name, "value": v, "min": 0.0},
                    hint="Crop fractions grow inward, so they cannot be negative.",
                )
        # Opposite sides must leave something behind.  Upstream does not check
        # this: it writes a MediaBox whose right edge is left of its left edge,
        # i.e. a negative-width page that renders blank.  Refuse instead.
        if vals["left"] + vals["right"] >= 1.0:
            raise crop_out_of_range("horizontal", vals["left"], vals["right"], page_no)
        if vals["top"] + vals["bottom"] >= 1.0:
            raise crop_out_of_range("vertical", vals["top"], vals["bottom"], page_no)
        return cls(**vals)


@dataclass(frozen=True)
class InputRef:
    """One source document referenced by the spec."""

    id: str
    path: Path
    password: str | None = None


@dataclass(frozen=True)
class PageSpec:
    """One output page: either a slice of an input, or a blank page."""

    index: int
    """1-based position in the output document."""
    input_id: str | None = None
    page: int | None = None
    rotate: int = 0
    scale: float = 1.0
    crop: Crop = field(default_factory=Crop)
    blank: bool = False
    width: float | None = None
    height: float | None = None
    underlay: "PageRef | None" = None
    overlay: "PageRef | None" = None
    open_end: bool = False
    """True when the spec asked for "N-" (page N through the last page).

    The end is only knowable once the document is open, so such entries are
    expanded by :func:`pdfarranger_api.plan.plan` rather than here.  Keeping the
    marker (instead of guessing) means spec validation stays side-effect free.
    """

    @property
    def is_blank(self) -> bool:
        return self.blank


@dataclass(frozen=True)
class PageRef:
    """A page of an input used as an underlay/overlay layer."""

    input_id: str
    page: int


@dataclass(frozen=True)
class Spec:
    """A fully validated spec."""

    version: int
    inputs: tuple[InputRef, ...]
    pages: tuple[PageSpec, ...]
    output: Path
    metadata: Mapping[str, str] = field(default_factory=dict)
    deterministic: bool = False
    output_password: str | None = None
    split: str | None = None

    def input_by_id(self) -> dict[str, InputRef]:
        return {i.id: i for i in self.inputs}


def parse_page_range(raw: Any, where: str) -> tuple[list[int], bool]:
    """Expand ``"1-3"``, ``"7"``, ``"1-3,5"`` or ``[1,2,3]`` into page numbers.

    Accepts a 1-based page number, an ``"a-b"`` range string, a comma-joined
    list of those, or a JSON array of numbers.  Ranges are inclusive and may be
    written either way round (``"3-1"`` means the same pages as ``"1-3"``).

    Returns ``(pages, open_end)``.  ``open_end`` is True when the expression
    ended with ``"-"`` (e.g. ``"3-"``), meaning "to the last page" - the caller
    resolves that once the document is open.
    """
    if isinstance(raw, int) and not isinstance(raw, bool):
        parts: list[Any] = [raw]
    elif isinstance(raw, str):
        parts = [p for p in re.split(r"[,;]", raw) if p.strip()]
    elif isinstance(raw, Sequence):
        parts = list(raw)
    else:
        raise spec_invalid(
            f"{where} must be a page number, a range string, or an array",
            where=where,
            got=type(raw).__name__,
        )

    out: list[int] = []
    open_end = False
    for part in parts:
        if isinstance(part, int) and not isinstance(part, bool):
            if part < 1:
                raise spec_invalid(
                    f"{where} contains page {part}; pages are 1-based",
                    where=where,
                    value=part,
                )
            out.append(part)
            continue
        if isinstance(part, str):
            m = _RANGE_RE.match(part)
            if not m:
                raise spec_invalid(
                    f"{where} has an unparsable page expression {part!r}",
                    where=where,
                    value=part,
                    expected="e.g. 3, 1-3, 1-3,7, or 3- (to the end)",
                )
            a = int(m.group(1))
            if m.group(2) == "":
                # "3-" : from page 3 to the end, resolved against the document.
                if a < 1:
                    raise spec_invalid(
                        f"{where} contains page 0; pages are 1-based",
                        where=where,
                        value=part,
                    )
                out.append(a)
                open_end = True
                continue
            b = int(m.group(2)) if m.group(2) else a
            if a < 1 or b < 1:
                raise spec_invalid(
                    f"{where} contains page 0; pages are 1-based",
                    where=where,
                    value=part,
                )
            lo, hi = (a, b) if a <= b else (b, a)
            out.extend(range(lo, hi + 1))
            continue
        raise spec_invalid(f"{where} has an unsupported entry {part!r}", where=where)

    if not out:
        raise ApiError(ErrorCode.NO_PAGES, f"{where} expands to no pages", details={"where": where})
    return out, open_end


def parse_spec(raw: Mapping[str, Any], *, base_dir: Path | None = None) -> Spec:
    """Validate ``raw`` and return a :class:`Spec`.

    Validation is complete and side-effect free: nothing is opened for writing
    and no page index is checked against a real document here (that needs the
    document, see :func:`pdfarranger_api.plan`).
    """
    if not isinstance(raw, Mapping):
        raise spec_invalid("spec must be a JSON object", got=type(raw).__name__)
    _reject_unknown(raw, _ALLOWED_TOP, "spec")

    version = raw.get("version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise ApiError(
            ErrorCode.SPEC_INVALID,
            f"unsupported spec version {version!r}; this build understands {SPEC_VERSION}",
            details={"version": version, "supported": [SPEC_VERSION]},
            hint="Regenerate the spec with the current version of this tool.",
        )

    base = Path(base_dir) if base_dir is not None else Path.cwd()

    raw_inputs = raw.get("inputs")
    if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, (str, bytes)):
        raise spec_invalid("spec.inputs must be an array", got=type(raw_inputs).__name__)
    if not raw_inputs:
        raise spec_invalid("spec.inputs must not be empty")

    inputs: list[InputRef] = []
    seen_ids: set[str] = set()
    for pos, item in enumerate(raw_inputs):
        where = f"inputs[{pos}]"
        if not isinstance(item, Mapping):
            raise spec_invalid(f"{where} must be an object", where=where)
        _reject_unknown(item, _ALLOWED_INPUT, where)
        ident = _as_str(item.get("id"), where, "id")
        if ident in seen_ids:
            raise spec_invalid(
                f"duplicate input id {ident!r}", where=where, id=ident
            )
        seen_ids.add(ident)
        path_raw = _as_str(item.get("path"), where, "path")
        path = Path(path_raw)
        if not path.is_absolute():
            path = (base / path).resolve()
        password = item.get("password")
        if password is not None and not isinstance(password, str):
            raise spec_invalid(f"{where}.password must be a string or null", where=where)
        inputs.append(InputRef(id=ident, path=path, password=password))

    raw_pages = raw.get("pages")
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
        raise spec_invalid("spec.pages must be an array", got=type(raw_pages).__name__)
    if not raw_pages:
        raise ApiError(ErrorCode.NO_PAGES, "spec.pages must not be empty")

    pages: list[PageSpec] = []
    for pos, item in enumerate(raw_pages):
        where = f"pages[{pos}]"
        if not isinstance(item, Mapping):
            raise spec_invalid(f"{where} must be an object", where=where)
        _reject_unknown(item, _ALLOWED_PAGE, where)
        pages.extend(_parse_page_entry(item, where, len(pages) + 1, seen_ids))

    output_raw = raw.get("output")
    if output_raw is None:
        raise spec_invalid("spec.output is required (a file path)")
    output = Path(_as_str(output_raw, "spec", "output"))
    if not output.is_absolute():
        output = (base / output).resolve()

    metadata: dict[str, str] = {}
    raw_meta = raw.get("metadata") or {}
    if not isinstance(raw_meta, Mapping):
        raise spec_invalid("spec.metadata must be an object")
    _reject_unknown(raw_meta, _ALLOWED_META, "metadata")
    for key, value in raw_meta.items():
        if not isinstance(value, str):
            raise spec_invalid(f"metadata.{key} must be a string", key=key)
        metadata[key] = value

    deterministic = raw.get("deterministic", False)
    if not isinstance(deterministic, bool):
        raise spec_invalid("spec.deterministic must be a boolean")

    output_password = raw.get("output_password")
    if output_password is not None and not isinstance(output_password, str):
        raise spec_invalid("spec.output_password must be a string or null")

    split = raw.get("split")
    if split is not None:
        if not isinstance(split, str) or split not in {"page", "none"}:
            raise spec_invalid(
                "spec.split must be 'page' or 'none'",
                got=repr(split),
            )

    return Spec(
        version=SPEC_VERSION,
        inputs=tuple(inputs),
        pages=tuple(pages),
        output=output,
        metadata=metadata,
        deterministic=deterministic,
        output_password=output_password,
        split=split if split != "none" else None,
    )


def _parse_page_entry(
    item: Mapping[str, Any], where: str, first_index: int, input_ids: set[str]
) -> list[PageSpec]:
    """Expand one ``pages[]`` entry into one PageSpec per selected page."""
    is_blank = item.get("blank", False)
    if not isinstance(is_blank, bool):
        raise spec_invalid(f"{where}.blank must be a boolean", where=where)

    if is_blank:
        if "input" in item or "page" in item:
            raise spec_invalid(
                f"{where} is blank, so it cannot also name an input page",
                where=where,
            )
        width = item.get("width")
        height = item.get("height")
        if width is None or height is None:
            raise spec_invalid(
                f"{where} is blank and needs both width and height (in points)",
                where=where,
            )
        width = _as_number(width, where, "width")
        height = _as_number(height, where, "height")
        for name, val in (("width", width), ("height", height)):
            if not 0 < val <= MAX_PAGE_POINTS:
                raise spec_invalid(
                    f"{where}.{name} must be in (0, {MAX_PAGE_POINTS}] points, got {val}",
                    where=where,
                    field=name,
                    value=val,
                )
        return [
            PageSpec(
                index=first_index,
                blank=True,
                width=width,
                height=height,
                rotate=_parse_rotate(item, where, first_index),
                scale=_parse_scale(item, where, first_index),
            )
        ]

    input_id = item.get("input")
    if input_id is None:
        raise spec_invalid(
            f"{where} must set 'input' + 'page', or 'blank': true with width/height",
            where=where,
        )
    input_id = _as_str(input_id, where, "input")
    if input_id not in input_ids:
        raise spec_invalid(
            f"{where} references unknown input {input_id!r}",
            where=where,
            input=input_id,
            known=sorted(input_ids),
        )
    if "page" not in item:
        raise spec_invalid(f"{where} must set 'page'", where=where)

    page_numbers, open_end = parse_page_range(item["page"], f"{where}.page")
    crop = Crop.parse(item.get("crop"), where, first_index)
    rotate = _parse_rotate(item, where, first_index)
    scale = _parse_scale(item, where, first_index)
    underlay = _parse_layer(item.get("underlay"), where, "underlay", input_ids)
    overlay = _parse_layer(item.get("overlay"), where, "overlay", input_ids)

    return [
        PageSpec(
            index=first_index + offset,
            input_id=input_id,
            page=pno,
            rotate=rotate,
            scale=scale,
            crop=crop,
            underlay=underlay,
            overlay=overlay,
            open_end=open_end and offset == len(page_numbers) - 1,
        )
        for offset, pno in enumerate(page_numbers)
    ]


def _parse_rotate(item: Mapping[str, Any], where: str, page_no: int) -> int:
    raw = item.get("rotate", 0)
    value = _as_number(raw, where, "rotate")
    if value % 90 != 0:
        raise angle_invalid(value, page_no)
    return int(value % 360)


def _parse_scale(item: Mapping[str, Any], where: str, page_no: int) -> float:
    raw = item.get("scale", 1.0)
    value = _as_number(raw, where, "scale")
    # Upstream happily writes a zero-size MediaBox for scale=0 or a negative
    # one for scale<0.  Both are unreadable output, so refuse them here.
    if value <= 0 or value > MAX_SCALE:
        raise scale_out_of_range(value, page_no, MAX_SCALE)
    return value


def _parse_layer(
    raw: Any, where: str, field_name: str, input_ids: set[str]
) -> PageRef | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise spec_invalid(f"{where}.{field_name} must be an object", where=where)
    _reject_unknown(raw, {"input", "page"}, f"{where}.{field_name}")
    input_id = _as_str(raw.get("input"), f"{where}.{field_name}", "input")
    if input_id not in input_ids:
        raise spec_invalid(
            f"{where}.{field_name} references unknown input {input_id!r}",
            where=where,
            input=input_id,
        )
    if "page" not in raw:
        raise spec_invalid(f"{where}.{field_name} must set 'page'", where=where)
    page, open_end = parse_page_range(raw["page"], f"{where}.{field_name}.page")
    if open_end or len(page) != 1:
        raise spec_invalid(
            f"{where}.{field_name}.page must select exactly one page",
            where=where,
            got=raw["page"],
        )
    return PageRef(input_id=input_id, page=page[0])
