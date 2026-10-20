"""Stable, machine-readable error contract.

Every failure this package can produce is an :class:`ApiError` carrying a
``code`` from :class:`ErrorCode`.  The codes are part of the public API: callers
are expected to branch on ``code``, not on message text.  Adding a code is a
minor version bump; renaming or removing one is a major version bump.

Design notes
------------
The upstream engine is a GUI program: it reports problems by popping dialogs and
otherwise fails silently.  Two upstream behaviours are *data-corrupting* rather
than merely noisy, and this module exists mostly to turn them into refusals:

``CROP_OUT_OF_RANGE`` / ``SCALE_OUT_OF_RANGE``
    Crop fractions are per-side and may not sum to >= 1.0 across an axis.  Pass
    ``Sides(0.6, 0.6, 0, 0)`` and the engine happily writes a page whose
    MediaBox is ``[367.2, 0, 244.8, 792]`` - right edge left of the left edge, a
    negative-width page that viewers render as blank.  ``scale=0`` and negative
    scales do the same.  Nothing raises; the output is simply broken.

``PAGE_OUT_OF_RANGE``
    An out-of-range page index surfaces as qpdf's
    ``parsing numeric range for ...`` message, which names the *input file* and
    not the offending spec entry.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class ErrorCode(str, Enum):
    """Every error code this package can emit."""

    # -- input / spec ------------------------------------------------------
    SPEC_INVALID = "SPEC_INVALID"
    """The spec document is not structurally valid (bad JSON, wrong types)."""

    SPEC_UNKNOWN_FIELD = "SPEC_UNKNOWN_FIELD"
    """The spec contains a field this version does not understand."""

    INPUT_NOT_FOUND = "INPUT_NOT_FOUND"
    """A referenced input file does not exist or is not a regular file."""

    INPUT_NOT_PDF = "INPUT_NOT_PDF"
    """A referenced input file is not a readable PDF."""

    INPUT_ENCRYPTED = "INPUT_ENCRYPTED"
    """The input is encrypted and no password was supplied."""

    INPUT_PASSWORD_WRONG = "INPUT_PASSWORD_WRONG"
    """A password was supplied but the input rejects it."""

    # -- geometry / selection ---------------------------------------------
    PAGE_OUT_OF_RANGE = "PAGE_OUT_OF_RANGE"
    """A page index does not exist in the referenced document."""

    NO_PAGES = "NO_PAGES"
    """The spec selects zero pages."""

    CROP_OUT_OF_RANGE = "CROP_OUT_OF_RANGE"
    """Crop fractions are negative, or sum to >= 1.0 along an axis."""

    SCALE_OUT_OF_RANGE = "SCALE_OUT_OF_RANGE"
    """Scale is zero, negative, or beyond the supported maximum."""

    ANGLE_INVALID = "ANGLE_INVALID"
    """Rotation is not a multiple of 90 degrees."""

    # -- output ------------------------------------------------------------
    OUTPUT_EXISTS = "OUTPUT_EXISTS"
    """The output path exists and overwriting was not requested."""

    OUTPUT_NOT_WRITABLE = "OUTPUT_NOT_WRITABLE"
    """The output directory is missing or not writable."""

    OUTPUT_SAME_AS_INPUT = "OUTPUT_SAME_AS_INPUT"
    """The output path collides with one of the inputs."""

    SPLIT_TOO_MANY_OUTPUTS = "SPLIT_TOO_MANY_OUTPUTS"
    """A split would produce more files than the configured limit."""

    # -- engine / environment ---------------------------------------------
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    """The vendored export engine could not be loaded."""

    ENGINE_INTEGRITY = "ENGINE_INTEGRITY"
    """The vendored engine sources do not match their audited hashes."""

    GUI_UNAVAILABLE = "GUI_UNAVAILABLE"
    """Something reached a GTK code path in this headless build."""

    INTERNAL_ERROR = "INTERNAL_ERROR"
    """An unexpected exception escaped; ``details`` carries the type name."""


#: Exit codes for the CLI.  Kept deliberately coarse: scripts branch on the
#: numeric exit status, structured consumers read ``error.code``.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_REFUSED = 4
EXIT_ENVIRONMENT = 5
EXIT_INTERNAL = 6

_EXIT_BY_CODE: Mapping[ErrorCode, int] = {
    ErrorCode.SPEC_INVALID: EXIT_USAGE,
    ErrorCode.SPEC_UNKNOWN_FIELD: EXIT_USAGE,
    ErrorCode.INPUT_NOT_FOUND: EXIT_INPUT,
    ErrorCode.INPUT_NOT_PDF: EXIT_INPUT,
    ErrorCode.INPUT_ENCRYPTED: EXIT_INPUT,
    ErrorCode.INPUT_PASSWORD_WRONG: EXIT_INPUT,
    ErrorCode.PAGE_OUT_OF_RANGE: EXIT_INPUT,
    ErrorCode.NO_PAGES: EXIT_USAGE,
    ErrorCode.CROP_OUT_OF_RANGE: EXIT_USAGE,
    ErrorCode.SCALE_OUT_OF_RANGE: EXIT_USAGE,
    ErrorCode.ANGLE_INVALID: EXIT_USAGE,
    ErrorCode.OUTPUT_EXISTS: EXIT_REFUSED,
    ErrorCode.OUTPUT_NOT_WRITABLE: EXIT_REFUSED,
    ErrorCode.OUTPUT_SAME_AS_INPUT: EXIT_REFUSED,
    ErrorCode.SPLIT_TOO_MANY_OUTPUTS: EXIT_REFUSED,
    ErrorCode.ENGINE_UNAVAILABLE: EXIT_ENVIRONMENT,
    ErrorCode.ENGINE_INTEGRITY: EXIT_ENVIRONMENT,
    ErrorCode.GUI_UNAVAILABLE: EXIT_ENVIRONMENT,
    ErrorCode.INTERNAL_ERROR: EXIT_INTERNAL,
}


class ApiError(Exception):
    """Base class for every error this package raises.

    Attributes
    ----------
    code:
        Stable machine-readable identifier.
    message:
        Human-readable, single-line summary.
    details:
        Structured extras (offending field, allowed range, file path, ...).
        Always JSON-serialisable.
    hint:
        Optional actionable next step.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
        self.hint = hint

    @property
    def exit_code(self) -> int:
        return _EXIT_BY_CODE.get(self.code, EXIT_INTERNAL)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "exit_code": self.exit_code,
        }
        if self.details:
            out["details"] = self.details
        if self.hint:
            out["hint"] = self.hint
        return out

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.code.value}] {self.message}"


def spec_invalid(message: str, **details: Any) -> ApiError:
    return ApiError(ErrorCode.SPEC_INVALID, message, details=details)


def unknown_field(field: str, where: str, allowed: list[str]) -> ApiError:
    return ApiError(
        ErrorCode.SPEC_UNKNOWN_FIELD,
        f"unknown field {field!r} in {where}",
        details={"field": field, "where": where, "allowed": allowed},
        hint="Remove the field, or check the version notes for a rename.",
    )


def page_out_of_range(file: str, page: int, page_count: int) -> ApiError:
    return ApiError(
        ErrorCode.PAGE_OUT_OF_RANGE,
        f"page {page} does not exist in {file!r} (it has {page_count} pages)",
        details={"file": file, "page": page, "page_count": page_count},
        hint=f"Valid page numbers are 1..{page_count}.",
    )


def crop_out_of_range(axis: str, a: float, b: float, page: int) -> ApiError:
    return ApiError(
        ErrorCode.CROP_OUT_OF_RANGE,
        f"crop on page {page} removes {a + b:.3f} of the {axis} axis "
        f"({a:.3f} + {b:.3f}); nothing would be left",
        details={
            "page": page,
            "axis": axis,
            "fractions": [a, b],
            "total": round(a + b, 6),
            "max_total": 1.0,
        },
        hint="Crop fractions are per side; opposite sides must sum to less than 1.0.",
    )


def scale_out_of_range(scale: float, page: int, maximum: float) -> ApiError:
    return ApiError(
        ErrorCode.SCALE_OUT_OF_RANGE,
        f"scale {scale} on page {page} is outside (0, {maximum}]",
        details={"page": page, "scale": scale, "min_exclusive": 0, "max": maximum},
        hint="A scale of 0 or a negative scale produces a zero-size page.",
    )


def angle_invalid(angle: float, page: int) -> ApiError:
    return ApiError(
        ErrorCode.ANGLE_INVALID,
        f"rotation {angle} on page {page} is not a multiple of 90",
        details={"page": page, "angle": angle, "allowed": [0, 90, 180, 270]},
    )


def output_exists(path: str) -> ApiError:
    return ApiError(
        ErrorCode.OUTPUT_EXISTS,
        f"output already exists: {path}",
        details={"path": path},
        hint="Pass --overwrite to replace it.",
    )
