"""Tests for the headless engine bootstrap.

The point of these tests is the *asymmetry* the shim relies on: the two GUI
classes in ``core.py`` must be definable, but any real GUI use must raise.  A
shim that quietly returns ``None`` would pass an "it imports" smoke test and then
produce wrong output the day upstream routes work through GTK.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pdfarranger_api import _engine


def test_gi_shim_is_installed_when_pygobject_is_absent():
    """The whole premise: no PyGObject, yet the engine still imports."""
    mode = _engine.ensure_gi()
    assert mode in {"real", "headless"}
    if mode == "headless":
        import gi  # noqa: F401  - must be importable

        from gi.repository import Gtk, Poppler  # noqa: F401


def test_engine_loads_and_exposes_the_symbols_we_call():
    engine = _engine.load_engine()
    for key in ("exporter", "core", "metadata", "outlines"):
        assert key in engine
    for name in ("export", "export_doc", "export_doc_job"):
        assert hasattr(engine["exporter"], name)
    for name in ("Page", "LayerPage", "Sides", "Dims"):
        assert hasattr(engine["core"], name)


def test_gui_classes_are_defined_but_unusable():
    """Subclassing works (needed at import); instantiating must raise."""
    engine = _engine.load_engine()
    core = engine["core"]
    assert hasattr(core, "PasswordDialog")
    assert hasattr(core, "PDFRenderer")

    with pytest.raises(_engine.GuiUnavailable):
        core.PasswordDialog(None, "x.pdf")


def test_gui_constants_are_not_silently_available():
    """A constant lookup must fail loudly, not hand back a placeholder object."""
    engine = _engine.load_engine()
    with pytest.raises(_engine.GuiUnavailable):
        engine["core"].Gtk.MessageType.WARNING


def test_gui_nested_call_fails_loudly():
    engine = _engine.load_engine()
    with pytest.raises(_engine.GuiUnavailable):
        engine["core"].Gtk.TreePath.new_from_indices([0])


def test_vendor_hashes_are_enforced(tmp_path: Path):
    """A tampered vendored file must be refused, not silently trusted."""
    pkg = Path(_engine.__file__).resolve().parent / "_vendor" / "pdfarranger"
    original = (pkg / "metadata.py").read_bytes()
    target = pkg / "metadata.py"
    try:
        target.write_bytes(original + b"\n# tampered\n")
        with pytest.raises(_engine.VendorIntegrityError) as excinfo:
            _engine._verify_vendor(pkg)
        assert "metadata.py" in str(excinfo.value)
    finally:
        target.write_bytes(original)


def test_engine_probe_reports_a_usable_engine():
    probe = _engine.engine_probe()
    assert probe["engine_loaded"] is True
    assert probe["error"] is None
    assert probe["pikepdf"]
    assert probe["upstream"]["version"] == "1.14.0"


def test_import_works_in_a_bare_subprocess_without_pygobject():
    """No PYTHONPATH tricks, no pre-imported gi: a fresh interpreter must cope."""
    repo = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import pdfarranger_api as p;"
        "caps = p.capabilities();"
        "print(caps['engine']['loaded'], caps['engine']['gi_mode'])"
    ) % repo
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    loaded, mode = proc.stdout.split()
    assert loaded == "True"
    assert mode in {"real", "headless"}


def test_import_does_not_require_a_display(monkeypatch):
    """Guard against someone adding a GTK init call to the import path."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert _engine.engine_probe()["engine_loaded"] is True


def test_cairo_shim_installs_when_pycairo_is_absent():
    """``core.py`` imports cairo at module scope for the thumbnail renderer only.

    pycairo is a compiled dependency that is absent on most headless boxes, so
    the same treatment as ``gi`` applies: importable, unusable.
    """
    mode = _engine.ensure_optional_graphics()
    assert mode in {"real", "headless"}
    if mode == "headless":
        import cairo

        # Module-level constants must resolve (they are read inside methods).
        assert cairo.FORMAT_ARGB32 is not None
        with pytest.raises(_engine.GuiUnavailable):
            cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)


def test_engine_probe_reports_graphics_mode():
    probe = _engine.engine_probe()
    assert "graphics" in probe
    assert probe["graphics"]["cairo"]["mode"] in {"real", "headless"}


def test_capabilities_reports_both_optional_dependencies():
    import pdfarranger_api as api

    engine = api.capabilities()["engine"]
    assert engine["loaded"] is True
    assert engine["gi_mode"] in {"real", "headless"}
    assert engine["cairo_mode"] in {"real", "headless"}


def test_engine_loads_when_cairo_and_gi_cannot_be_imported():
    """Simulate a machine with neither pycairo nor PyGObject.

    Done in a subprocess with a ``find_spec``-based import blocker, because
    patching ``__import__`` in-process leaks into other tests and misses the
    ``from X import Y`` form.  A meta-path finder is the only reliable way to
    make an import look genuinely absent.
    """
    repo = Path(__file__).resolve().parents[1]
    code = r"""
import sys
sys.path.insert(0, r'{repo}')

BLOCKED = {{'gi', 'cairo'}}

class Blocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in BLOCKED:
            raise ImportError('simulated absence of ' + fullname)
        return None

sys.meta_path.insert(0, Blocker())
for mod in list(sys.modules):
    if mod.split('.')[0] in BLOCKED:
        del sys.modules[mod]

from pdfarranger_api._engine import engine_probe
probe = engine_probe()
assert probe['engine_loaded'] is True, probe['error']
assert probe['gi']['mode'] == 'headless', probe['gi']
assert probe['graphics']['cairo']['mode'] == 'headless', probe['graphics']

# And the shims must be usable-but-loud.
import cairo
try:
    cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
except RuntimeError as exc:
    print('OK', probe['gi']['mode'], probe['graphics']['cairo']['mode'])
else:
    raise SystemExit('cairo shim did not raise')
""".format(repo=str(repo))

    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK headless headless" in proc.stdout
