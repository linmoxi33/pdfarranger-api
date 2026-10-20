"""PDF Arranger 1.14.0 - vendored subset (GPL-3.0-or-later).

Upstream: https://github.com/pdfarranger/pdfarranger
License : see COPYING in this directory.

Only the modules reachable from the export path are included:
``core``, ``exporter``, ``exporter_outlines``, ``metadata``.  The GUI entry
point (``pdfarranger.py``), thumbnail renderer and dialogs are not vendored.

These files are byte-identical to upstream and are pinned by sha256 in
``pdfarranger_api._engine.VENDOR_HASHES``.  Do not edit them; patch behaviour in
``pdfarranger_api`` instead.
"""

__all__: list[str] = []
