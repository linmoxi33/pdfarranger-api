#!/usr/bin/env python3
"""Inspect a PDF, then build a spec from what was found.

This is the pattern for "process whatever lands in a directory": measure first,
then decide, rather than hard-coding page counts.

Run:  python examples/03_inspect_before_spec.py
"""

from pathlib import Path

import pdfarranger_api as api

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parent / "samples"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

info = api.inspect_pdf(SAMPLES / "outlines.pdf")
print(f"{Path(info['path']).name}: {info['page_count']} pages, "
      f"PDF {info['pdf_version']}, {info['size_bytes']} bytes")
for page in info["pages"]:
    print(f"  page {page['index']}: {page['width_mm']:.1f} x "
          f"{page['height_mm']:.1f} mm")

# Take the last page and the first page, in that order.
n = info["page_count"]
result = api.run(
    {
        "inputs": [{"id": "src", "path": info["path"]}],
        "pages": [{"input": "src", "page": [n, 1]}],
        "output": str(OUT / "ends.pdf"),
    },
    overwrite=True,
)
print(f"\nwrote {result.outputs[0].name} with pages [{n}, 1]")
