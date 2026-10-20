#!/usr/bin/env python3
"""Merge two PDFs and reverse one of them, then check the result.

Run:  python examples/01_merge_reorder.py
"""

from pathlib import Path

import pikepdf

import pdfarranger_api as api

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parent / "samples"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

spec = {
    "inputs": [
        {"id": "report", "path": str(SAMPLES / "basic.pdf")},
        {"id": "bookmarks", "path": str(SAMPLES / "outlines.pdf")},
    ],
    "pages": [
        # First two pages of the report, unmodified.
        {"input": "report", "page": "1-2"},
        # The bookmarked document, back to front.
        {"input": "bookmarks", "page": "4-1"},
        # A blank separator sheet.
        {"blank": True, "width": 595, "height": 842},
    ],
    "output": str(OUT / "merged.pdf"),
    "metadata": {"title": "Merged report", "author": "pdfarranger-api"},
}

result = api.run(spec, overwrite=True)
print(f"wrote {result.outputs[0].name}: {result.page_count} pages, "
      f"{result.bytes_written} bytes")

with pikepdf.open(result.outputs[0]) as pdf:
    for i, page in enumerate(pdf.pages, start=1):
        box = page.mediabox
        w = float(box[2]) - float(box[0])
        h = float(box[3]) - float(box[1])
        print(f"  page {i}: {w:.0f} x {h:.0f} pt, rotate {page.get('/Rotate', 0)}")
