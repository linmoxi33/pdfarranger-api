#!/usr/bin/env python3
"""Crop, rotate and scale pages - and see what gets refused.

Run:  python examples/02_crop_and_rotate.py
"""

from pathlib import Path

import pdfarranger_api as api

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parent / "samples"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

# A crop that removes 10% left, 20% right, 5% top and bottom.
result = api.run(
    {
        "inputs": [{"id": "a", "path": str(SAMPLES / "basic.pdf")}],
        "pages": [
            {"input": "a", "page": 1,
             "crop": {"left": 0.1, "right": 0.2, "top": 0.05, "bottom": 0.05}},
            {"input": "a", "page": 2, "rotate": 90, "scale": 0.75},
        ],
        "output": str(OUT / "cropped.pdf"),
    },
    overwrite=True,
)
print(f"cropped + rotated: {result.page_count} pages -> {result.outputs[0].name}")

# The same operation with an impossible crop is refused before anything is written.
print("\nnow the refusal:")
try:
    api.run(
        {
            "inputs": [{"id": "a", "path": str(SAMPLES / "basic.pdf")}],
            "pages": [{"input": "a", "page": 1,
                       "crop": {"left": 0.6, "right": 0.6}}],
            "output": str(OUT / "impossible.pdf"),
        }
    )
except api.ApiError as err:
    print(f"  {err.code.value}: {err.message}")
    print(f"  details: {err.details}")
    print(f"  hint:    {err.hint}")
    print("  (PDF Arranger itself would have written a negative-width page here)")
