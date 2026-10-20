"""pdfarranger-api: a programmatic interface to PDF Arranger's page engine.

PDF Arranger is a GTK application: merging, reordering, cropping and rotating
pages all happen by dragging thumbnails.  Its engine (pure pikepdf) is perfectly
capable of running in a batch job, but it cannot even be *imported* without GTK,
and it reports geometry mistakes by silently writing a broken page.

This package puts a validated, headless API in front of that engine:

>>> from pdfarranger_api import run
>>> run({"inputs": [{"id": "a", "path": "in.pdf"}],
...      "pages": [{"input": "a", "page": 1, "rotate": 90}],
...      "output": "out.pdf"})            # doctest: +SKIP

Guarantees
----------
* Imports and runs without GTK, X11 or a display.
* Every failure raises :class:`~pdfarranger_api.errors.ApiError` with a stable
  ``code`` - no pikepdf or qpdf exceptions escape.
* Outputs are written atomically; a failed run leaves nothing behind.
* ``deterministic=True`` produces byte-identical files across runs.
* Existing files are never overwritten unless explicitly allowed.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .api import (  # noqa: E402
    ApplyResult,
    Crop,
    InputInfo,
    PageInfo,
    Plan,
    Spec,
    apply,
    capabilities,
    dry_run,
    inspect_pdf,
    merge,
    plan,
    reorder,
    run,
    split_pages,
)
from .errors import ApiError, ErrorCode  # noqa: E402
from .spec import SPEC_VERSION  # noqa: E402

__all__ = [
    "__version__",
    "ApiError",
    "ApplyResult",
    "Crop",
    "ErrorCode",
    "InputInfo",
    "PageInfo",
    "Plan",
    "SPEC_VERSION",
    "Spec",
    "apply",
    "capabilities",
    "dry_run",
    "inspect_pdf",
    "merge",
    "plan",
    "reorder",
    "run",
    "split_pages",
]
