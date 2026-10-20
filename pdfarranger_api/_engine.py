"""Headless bootstrap for the vendored PDF Arranger export engine.

PDF Arranger is a GTK application.  Its page-geometry engine, however, is pure
pikepdf code.  The only thing that stops ``import pdfarranger.exporter`` from
working on a machine without GTK is that ``pdfarranger/core.py`` executes two
GUI class definitions at *module* scope::

    class PasswordDialog(Gtk.Dialog): ...
    class PDFRenderer(threading.Thread, GObject.GObject): ...

Both are dead weight for a batch/API use case: nothing on the export path ever
instantiates them.  ``gi`` is therefore optional at import time here.

Two layers, deliberately kept apart:

``ensure_gi()``
    Installs a *sentinel* ``gi`` module into ``sys.modules`` **only if the real
    PyGObject is absent**.  The sentinel satisfies ``import gi`` and
    ``from gi.repository import Gtk`` and lets the two GUI classes be *defined*,
    but any actual attribute access or call on a GUI symbol raises
    :class:`GuiUnavailable`.  A silent no-op stub would let a future upstream
    change route real work through GTK and fail invisibly; the sentinel fails
    loudly instead.

``ensure_optional_graphics()``
    ``core.py`` also does a bare ``import cairo`` at module scope, used only by
    the thumbnail renderer (``PDFRenderer.update``).  pycairo is a compiled
    dependency that is absent on most headless boxes, and nothing on the export
    path touches it - so it gets the same treatment: a sentinel that imports fine
    and raises on use.  Only cairo's *drawing* calls are guarded; the export path
    never reaches them.

``load_engine()``
    Imports the vendored modules and asserts that the pieces we rely on are the
    ones we audited (by source hash).  If someone edits the vendored files, the
    load fails with :class:`VendorIntegrityError` rather than producing subtly
    different PDFs.
"""

from __future__ import annotations

import hashlib
import sys
import threading
from types import ModuleType
from typing import Any

__all__ = [
    "GuiUnavailable",
    "VendorIntegrityError",
    "EngineUnavailable",
    "ensure_gi",
    "ensure_optional_graphics",
    "graphics_mode",
    "load_engine",
    "engine_probe",
    "UPSTREAM",
]


class GuiUnavailable(RuntimeError):
    """Raised when code under the headless shim tries to touch GTK for real."""


class VendorIntegrityError(RuntimeError):
    """Raised when the vendored engine sources do not match the audited hashes."""


class EngineUnavailable(RuntimeError):
    """Raised when the export engine cannot be loaded at all."""


#: Provenance of the vendored sources.  Keep in sync with ``_vendor/pdfarranger``.
UPSTREAM = {
    "project": "pdfarranger",
    "url": "https://github.com/pdfarranger/pdfarranger",
    "version": "1.14.0",
    "license": "GPL-3.0-or-later",
    "tag_commit": "main @ 2026-10-01",
}

#: sha256 of every vendored file, as audited.  See ``tools/verify_vendor.py``.
VENDOR_HASHES = {
    "core.py": "03c5b72e7f9fc43935f2c96a89bc8582789b0236519498dc62978471c499ebe7",
    "exporter.py": "1247fc1e9ff7c16be495e4516170c6722c8167fe8a6e9b8645c63f9871f1acc9",
    "exporter_outlines.py": "d3c605e0d758c4d2803c9e0ec67fdc252eaaa3db04e4e164185ab15fe61d4f1e",
    "metadata.py": "f87f5ddfa1d61be55a357fb5351b37b818b6af384617555fdccadb1482a40300",
}

_GI_LOCK = threading.Lock()
_GI_STATE: dict[str, Any] = {"installed": False, "mode": None, "reason": None}


class _StrictMeta(type):
    """Metaclass for a GUI symbol: subclassing is allowed, use is not.

    ``core.py`` needs ``class PasswordDialog(Gtk.Dialog)`` to *execute*, so the
    symbol must be usable as a base class.  Every other interaction - reading a
    nested constant (``Gtk.MessageType.WARNING``), instantiating it, or calling
    it - raises :class:`GuiUnavailable`.  That asymmetry is the point: the two
    module-level GUI classes get defined, while anything that would genuinely
    need GTK fails loudly instead of silently doing nothing.
    """

    def __getattr__(cls, name: str) -> Any:
        raise GuiUnavailable(
            f"gi.{cls.__name__}.{name} is unavailable: this is a headless build "
            "without PyGObject"
        )

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        raise GuiUnavailable(
            f"gi.{cls.__name__} cannot be instantiated: this is a headless build "
            "without PyGObject"
        )


class _LooseMeta(type):
    """Metaclass for a GI *namespace*: attribute access yields a strict symbol."""

    def __getattr__(cls, name: str) -> Any:
        return _symbol(f"{cls.__name__}.{name}")


def _symbol(dotted: str) -> Any:
    """Build a strict GUI symbol named after ``dotted``."""
    return _StrictMeta(dotted, (), {"__module__": "gi.repository"})


class _PopplerNamespace(metaclass=_LooseMeta):
    """``Poppler`` with the one query ``core.py`` performs at import time."""

    __module__ = "gi.repository"

    @staticmethod
    def get_version() -> str:
        return "0.0.0-headless"


def _build_gi_shim() -> ModuleType:
    gi = ModuleType("gi")
    gi.__doc__ = (
        "Headless sentinel for PyGObject installed by pdfarranger-api. "
        "Subclassing a GI type works; using one raises GuiUnavailable."
    )

    def require_version(namespace: str, version: str) -> None:
        return None

    gi.require_version = require_version  # type: ignore[attr-defined]

    repo = ModuleType("gi.repository")
    repo.__doc__ = "Headless sentinel namespace for GI types."
    for _name in ("Gtk", "GObject", "GLib", "Gio", "Pango", "Gdk", "GdkPixbuf"):
        setattr(repo, _name, _LooseMeta(_name, (), {"__module__": "gi.repository"}))

    # ``core.py`` calls ``Poppler.get_version()`` at import time purely to
    # populate the POPPLER_VERSION constant, which the export path never reads.
    # Answer that one query instead of forcing callers to special-case it.
    repo.Poppler = _PopplerNamespace  # type: ignore[attr-defined]
    gi.repository = repo  # type: ignore[attr-defined]

    return gi


def ensure_gi() -> str:
    """Make ``import gi`` work, preferring the real PyGObject.

    Returns the mode in use: ``"real"`` or ``"headless"``.
    """
    with _GI_LOCK:
        if _GI_STATE["installed"]:
            return _GI_STATE["mode"]

        try:  # A genuine PyGObject install always wins - never shadow it.
            import gi as real_gi  # noqa: F401

            _GI_STATE.update(installed=True, mode="real", reason="PyGObject importable")
            return "real"
        except Exception as exc:  # ImportError or a broken native build
            _GI_STATE["reason"] = f"{type(exc).__name__}: {exc}"

        if "gi" not in sys.modules:
            sys.modules["gi"] = _build_gi_shim()
            sys.modules["gi.repository"] = sys.modules["gi"].repository
        _GI_STATE.update(installed=True, mode="headless")
        return "headless"


_CAIRO_STATE: dict[str, Any] = {"installed": False, "mode": None, "reason": None}


def _build_cairo_shim() -> ModuleType:
    """Sentinel for pycairo: constants resolve, drawing calls raise.

    ``core.py`` reads module-level constants (``cairo.FORMAT_ARGB32``) only inside
    the thumbnail renderer, and calls ``cairo.ImageSurface`` / ``cairo.Context``
    there.  Exposing the constants as opaque sentinels keeps any accidental
    import-time reference from crashing, while the calls still fail loudly if a
    future change routes export work through the renderer.
    """
    cairo = ModuleType("cairo")
    cairo.__doc__ = (
        "Headless sentinel for pycairo installed by pdfarranger-api. "
        "Drawing calls raise GuiUnavailable."
    )
    for _const in (
        "FORMAT_ARGB32",
        "FORMAT_RGB24",
        "FILL_RULE_EVEN_ODD",
        "FILL_RULE_WINDING",
        "ANTIALIAS_DEFAULT",
        "OPERATOR_OVER",
    ):
        setattr(cairo, _const, object())

    for _cls in ("ImageSurface", "Context", "Matrix", "Surface"):
        # ``_symbol`` returns a class whose *instantiation* raises GuiUnavailable
        # (the metaclass defines __call__).  Using ``_LooseMeta`` here instead
        # would produce a plain class and a bare TypeError on use - which is why
        # the subprocess test asserts the exception type, not just "it raises".
        setattr(cairo, _cls, _symbol(f"cairo.{_cls}"))
    return cairo


def ensure_optional_graphics() -> str:
    """Make ``import cairo`` work when pycairo is not installed.

    Returns ``"real"`` or ``"headless"``.  pycairo is only needed to render
    thumbnails in the GUI; the export path never imports it for use.
    """
    if _CAIRO_STATE["installed"]:
        return _CAIRO_STATE["mode"]
    try:
        import cairo as real_cairo  # noqa: F401

        _CAIRO_STATE.update(installed=True, mode="real", reason="pycairo importable")
        return "real"
    except Exception as exc:
        _CAIRO_STATE["reason"] = f"{type(exc).__name__}: {exc}"
    if "cairo" not in sys.modules:
        sys.modules["cairo"] = _build_cairo_shim()
    _CAIRO_STATE.update(installed=True, mode="headless")
    return "headless"


def graphics_mode() -> dict[str, Any]:
    """Describe how the optional graphics dependencies were resolved."""
    return {"cairo": dict(_CAIRO_STATE)}


def gi_mode() -> dict[str, Any]:
    """Describe how ``gi`` was resolved.  Populated after :func:`ensure_gi`."""
    return dict(_GI_STATE)


def _verify_vendor(pkg_dir: Any) -> None:
    for name, expected in VENDOR_HASHES.items():
        path = pkg_dir / name
        if not path.is_file():
            raise VendorIntegrityError(f"vendored file missing: {path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise VendorIntegrityError(
                f"vendored {name} does not match the audited hash "
                f"(expected {expected[:12]}..., got {actual[:12]}...)"
            )


_ENGINE: dict[str, Any] = {}
_ENGINE_LOCK = threading.Lock()


def load_engine(verify: bool = True) -> dict[str, Any]:
    """Import the vendored engine and return the handles we use.

    The result is a dict with ``exporter``, ``core``, ``metadata``,
    ``outlines`` module objects plus ``Page``, ``Sides`` and ``Dims`` classes.
    """
    with _ENGINE_LOCK:
        if _ENGINE:
            return _ENGINE

        ensure_gi()
        ensure_optional_graphics()
        from pathlib import Path

        pkg_dir = Path(__file__).resolve().parent / "_vendor" / "pdfarranger"
        if verify:
            _verify_vendor(pkg_dir)

        try:
            from . import _vendor as _vendor_pkg
            from ._vendor.pdfarranger import core as _core
            from ._vendor.pdfarranger import exporter as _exporter
            from ._vendor.pdfarranger import exporter_outlines as _outlines
            from ._vendor.pdfarranger import metadata as _metadata
        except Exception as exc:
            raise EngineUnavailable(
                f"cannot import the vendored PDF Arranger engine: {type(exc).__name__}: {exc}"
            ) from exc

        missing = [
            n
            for n, obj in (
                ("exporter.export", getattr(_exporter, "export", None)),
                ("exporter.export_doc", getattr(_exporter, "export_doc", None)),
                ("core.Page", getattr(_core, "Page", None)),
                ("core.Sides", getattr(_core, "Sides", None)),
                ("core.Dims", getattr(_core, "Dims", None)),
                ("metadata.merge_doc", getattr(_metadata, "merge_doc", None)),
                ("exporter_outlines.rebuild_outlines",
                 getattr(_outlines, "rebuild_outlines", None)),
            )
            if obj is None
        ]
        if missing:
            raise EngineUnavailable(
                "vendored engine is missing expected symbols: " + ", ".join(missing)
            )

        _ENGINE.update(
            exporter=_exporter,
            core=_core,
            metadata=_metadata,
            outlines=_outlines,
            Page=_core.Page,
            Sides=_core.Sides,
            Dims=_core.Dims,
            vendor_package=_vendor_pkg,
        )
        return _ENGINE


def engine_probe() -> dict[str, Any]:
    """Report whether the engine is usable, without raising."""
    info: dict[str, Any] = {
        "upstream": dict(UPSTREAM),
        "gi": gi_mode(),
        "graphics": graphics_mode(),
        "engine_loaded": False,
        "pikepdf": None,
        "error": None,
    }
    try:
        import pikepdf

        info["pikepdf"] = pikepdf.__version__
    except Exception as exc:  # pragma: no cover - pikepdf is a hard dependency
        info["error"] = f"pikepdf unavailable: {exc}"
        return info
    try:
        load_engine()
        info["engine_loaded"] = True
        info["gi"] = gi_mode()
        info["graphics"] = graphics_mode()
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info
