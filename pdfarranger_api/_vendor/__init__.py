"""Vendored third-party sources, unmodified.

``_vendor/pdfarranger`` contains four files copied verbatim from PDF Arranger
1.14.0 (GPL-3.0-or-later, see ``_vendor/pdfarranger/COPYING``).  They are
shipped unmodified on purpose: this project adds an API in front of the engine,
it does not fork it.  ``pdfarranger_api._engine.VENDOR_HASHES`` pins each file
so an accidental edit is caught at import time.

Why vendor instead of depending on the package: PDF Arranger is distributed by
Linux distributions and on GitHub releases, but it is *not* published on PyPI,
so there is nothing to ``pip install``.  Copying the four pure-Python modules
that the export path needs is the only way to get a reproducible install.
"""

__all__ = []
