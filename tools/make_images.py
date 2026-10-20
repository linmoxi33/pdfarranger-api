#!/usr/bin/env python3
"""Generate the README images with Pillow.

Two assets:

``hero.png``       1200x400 banner: what the package is, and the three facts that
                   justify its existence (no GTK, no pycairo, guards bad geometry).
``architecture.png`` 1200x620 diagram: upstream engine -> sentinels -> our layers.

Everything is drawn from primitives; no external assets.  Palette follows the
project's warm editorial look (espresso / amber / terracotta) on a light canvas,
because the README is read on light backgrounds even when the editor is dark.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "docs"
OUT.mkdir(exist_ok=True)

# -- palette ---------------------------------------------------------------
INK = (38, 32, 28)
INK_SOFT = (92, 82, 74)
INK_FAINT = (150, 140, 131)
PAPER = (252, 249, 245)
PANEL = (255, 255, 255)
LINE = (226, 217, 207)
ESPRESSO = (62, 43, 34)
AMBER = (198, 124, 40)
AMBER_SOFT = (250, 238, 220)
TERRA = (176, 74, 44)
TERRA_SOFT = (252, 232, 226)
SAGE = (78, 110, 84)
SAGE_SOFT = (232, 240, 231)
SLATE = (72, 84, 104)
SLATE_SOFT = (234, 238, 244)

FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def mono(size: int) -> ImageFont.FreeTypeFont:
    return font("consola.ttf", size)


def mono_bold(size: int) -> ImageFont.FreeTypeFont:
    return font("consolab.ttf", size)


def sans(size: int) -> ImageFont.FreeTypeFont:
    # msyh.ttc index 0 = Microsoft YaHei regular; covers Latin + CJK cleanly.
    return ImageFont.truetype(str(FONTS / "msyh.ttc"), size, index=0)


def sans_bold(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / "msyhbd.ttc"), size, index=0)


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text_size(draw: ImageDraw.ImageDraw, text: str, f) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=f)
    return right - left, bottom - top


def fit_font(draw, text: str, factory, start: int, minimum: int, max_width: int):
    """Largest size from ``start`` down to ``minimum`` whose text fits ``max_width``.

    Hand-picked font sizes are how text ends up running under a panel: the
    rendered width depends on the face, not on the point size.  Measure instead.
    """
    size = start
    while size > minimum:
        f = factory(size)
        if text_size(draw, text, f)[0] <= max_width:
            return f, size
        size -= 1
    return factory(minimum), minimum


# ==========================================================================
# hero
# ==========================================================================


def build_hero() -> None:
    W, H = 1200, 400
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    # A warm wash on the left, fading out: keeps the banner from looking flat.
    for x in range(0, 520):
        t = x / 520
        blend = 0.55 * (1 - t) ** 1.6
        row = tuple(
            int(PAPER[i] * (1 - blend) + AMBER_SOFT[i] * blend) for i in range(3)
        )
        d.line([(x, 0), (x, H)], fill=row)

    # Top rule.
    d.rectangle([0, 0, W, 5], fill=AMBER)

    # Right panel: a real terminal transcript (values from an actual run).
    # Reserve its space first, then lay the text out inside what is left.
    px, py, pw, ph = 700, 62, 448, 276
    text_right = px - 44  # hard boundary for everything on the left

    x0 = 64
    y = 54

    # Eyebrow.
    d.text((x0, y), "PDF ARRANGER  ·  HEADLESS API", font=mono_bold(17), fill=AMBER)
    y += 34

    # Title.
    title_font, _ = fit_font(d, "pdfarranger-api", mono_bold, 64, 44, text_right - x0)
    d.text((x0, y), "pdfarranger-api", font=title_font, fill=ESPRESSO)
    y += 84

    # Subtitle, measured to fit beside the panel rather than guessed.
    sub_font, _ = fit_font(
        d, "Merge, reorder, rotate, crop and split PDFs", sans, 25, 16, text_right - x0
    )
    d.text((x0, y), "Merge, reorder, rotate, crop and split PDFs", font=sub_font, fill=INK)
    y += 38
    d.text(
        (x0, y),
        "from code. No GTK. No pycairo. No display.",
        font=sub_font,
        fill=TERRA,
    )
    y += 48

    # Feature chips, sized to the space actually available.
    chips = [
        ("upstream 1.14.0", "sha256-pinned", AMBER, AMBER_SOFT),
        ("130 tests", "12 mutations killed", SAGE, SAGE_SOFT),
        ("atomic writes", "os.replace", SLATE, SLATE_SOFT),
    ]
    cx = x0
    for title, sub, colour, soft in chips:
        tw, _ = text_size(d, title, sans_bold(15))
        sw, _ = text_size(d, sub, sans(12))
        w = max(tw, sw) + 30
        rounded(d, [cx, y, cx + w, y + 58], 10, fill=PANEL, outline=LINE, width=1)
        d.rectangle([cx, y + 11, cx + 4, y + 47], fill=colour)
        d.text((cx + 16, y + 11), title, font=sans_bold(15), fill=colour)
        d.text((cx + 16, y + 34), sub, font=sans(12), fill=INK_FAINT)
        cx += w + 10

    d.rectangle([px + 6, py + 8, px + pw + 6, py + ph + 8], fill=(238, 231, 222))
    rounded(d, [px, py, px + pw, py + ph], 12, fill=ESPRESSO)
    # window dots
    for i, c in enumerate([(232, 106, 92), (226, 176, 84), (140, 178, 122)]):
        d.ellipse([px + 20 + i * 20, py + 18, px + 30 + i * 20, py + 28], fill=c)
    d.text(
        (px + 96, py + 14),
        "pdfarranger-api apply",
        font=mono(13),
        fill=(176, 160, 148),
    )
    d.line([(px, py + 42), (px + pw, py + 42)], fill=(86, 66, 55))

    lines = [
        ('{ "ok": true,', (214, 200, 188)),
        ('  "command": "apply",', (214, 200, 188)),
        ('  "result": {', (214, 200, 188)),
        ('    "page_count": 4,', (232, 214, 160)),
        ('    "bytes_written": 3968,', (232, 214, 160)),
        ('    "deterministic": true', (232, 214, 160)),
        ("  } }", (214, 200, 188)),
    ]
    ly = py + 58
    for text, colour in lines:
        d.text((px + 24, ly), text, font=mono(15), fill=colour)
        ly += 26

    d.text(
        (px + 24, py + ph - 34),
        "exit 0",
        font=mono_bold(14),
        fill=(150, 200, 150),
    )

    img.save(OUT / "hero.png", optimize=True)
    print(f"wrote {OUT / 'hero.png'}  {img.size}")


# ==========================================================================
# architecture
# ==========================================================================


def build_architecture() -> None:
    W, H = 1200, 660
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 5], fill=AMBER)

    d.text((56, 40), "HOW IT FITS TOGETHER", font=mono_bold(15), fill=AMBER)
    d.text(
        (56, 66),
        "Same engine. Different entrance.",
        font=sans_bold(30),
        fill=ESPRESSO,
    )
    d.text(
        (56, 112),
        "Nothing on the export path needs a window, so the only work is getting past two import-time GUI definitions.",
        font=sans(16),
        fill=INK_SOFT,
    )

    # -- layer 1: your code -------------------------------------------------
    y = 168
    rounded(d, [56, y, W - 56, y + 96], 12, fill=PANEL, outline=LINE, width=1)
    d.rectangle([56, y + 16, 60, y + 80], fill=SLATE)
    d.text((84, y + 16), "YOUR CODE", font=mono_bold(14), fill=SLATE)
    boxes = [
        ("CLI", "pdfarranger-api apply"),
        ("SDK", "api.run(spec)"),
        ("spec", '{"pages": [...]}'),
    ]
    bx = 84
    for title, sample in boxes:
        tw, _ = text_size(d, sample, mono(14))
        label_w = text_size(d, title, sans_bold(12))[0]
        w = label_w + tw + 44
        rounded(d, [bx, y + 40, bx + w, y + 80], 7, fill=SLATE_SOFT)
        d.text((bx + 12, y + 53), title, font=sans_bold(12), fill=SLATE)
        d.text((bx + 12 + label_w + 10, y + 51), sample, font=mono(14), fill=INK)
        bx += w + 12

    # arrow
    d.line([(W // 2, y + 96), (W // 2, y + 120)], fill=INK_FAINT, width=2)
    d.polygon([(W // 2 - 6, y + 118), (W // 2 + 6, y + 118), (W // 2, y + 128)], fill=INK_FAINT)

    # -- layer 2: our validation -------------------------------------------
    y = 292
    rounded(d, [56, y, W - 56, y + 118], 12, fill=TERRA_SOFT, outline=(226, 190, 180), width=1)
    d.rectangle([56, y + 16, 60, y + 102], fill=TERRA)
    d.text((84, y + 18), "pdfarranger_api  ·  validation + execution", font=mono_bold(14), fill=TERRA)
    items = [
        ("spec.py", "rejects crop ≥ 1.0,\nscale ≤ 0, bad angles"),
        ("plan.py", "opens inputs, checks\npage indices, no writes"),
        ("execute.py", "atomic replace,\ndeterministic bytes"),
    ]
    bx = 84
    for name, desc in items:
        w = 330
        rounded(d, [bx, y + 44, bx + w, y + 106], 8, fill=PANEL, outline=(232, 206, 198))
        d.text((bx + 14, y + 52), name, font=mono_bold(15), fill=TERRA)
        for i, line in enumerate(desc.split("\n")):
            d.text((bx + 14, y + 74 + i * 15), line, font=sans(12), fill=INK_SOFT)
        bx += w + 14

    # arrow
    d.line([(W // 2, y + 118), (W // 2, y + 142)], fill=INK_FAINT, width=2)
    d.polygon([(W // 2 - 6, y + 140), (W // 2 + 6, y + 140), (W // 2, y + 150)], fill=INK_FAINT)

    # -- layer 3: sentinels ------------------------------------------------
    y = 318 + 118
    rounded(d, [56, y, W - 56, y + 100], 12, fill=AMBER_SOFT, outline=(232, 208, 176), width=1)
    d.rectangle([56, y + 16, 60, y + 84], fill=AMBER)
    d.text((84, y + 16), "HEADLESS SENTINELS", font=mono_bold(14), fill=AMBER)
    d.text(
        (84, y + 40),
        "Importable, unusable. Subclassing works so the module-level GUI classes still define; any real call raises GuiUnavailable.",
        font=sans(13),
        fill=INK_SOFT,
    )
    badges = [
        ("gi", "PyGObject"),
        ("gi.repository", "Gtk · GObject · Poppler"),
        ("cairo", "pycairo"),
    ]
    bx = 84
    for mod, what in badges:
        mod_w = text_size(d, mod, mono_bold(14))[0]
        what_w = text_size(d, what, sans(12))[0]
        w = mod_w + what_w + 34
        rounded(d, [bx, y + 66, bx + w, y + 94], 6, fill=PANEL, outline=(232, 208, 176))
        d.text((bx + 12, y + 71), mod, font=mono_bold(14), fill=AMBER)
        d.text((bx + 22 + mod_w, y + 74), what, font=sans(12), fill=INK_FAINT)
        bx += w + 12

    # arrow
    d.line([(W // 2, y + 100), (W // 2, y + 122)], fill=INK_FAINT, width=2)
    d.polygon([(W // 2 - 6, y + 120), (W // 2 + 6, y + 120), (W // 2, y + 130)], fill=INK_FAINT)

    # -- layer 4: upstream -------------------------------------------------
    y = 318 + 118 + 100 + 22
    rounded(d, [56, y, W - 56, y + 92], 12, fill=PANEL, outline=LINE, width=1)
    d.rectangle([56, y + 16, 60, y + 76], fill=SAGE)
    d.text((84, y + 18), "UPSTREAM  ·  PDF ARRANGER 1.14.0", font=mono_bold(14), fill=SAGE)
    d.text(
        (84, y + 44),
        "exporter.export()  ·  core.Page  ·  exporter_outlines.rebuild_outlines()",
        font=mono(14),
        fill=INK,
    )
    d.text(
        (84, y + 66),
        "4 modules vendored byte-for-byte, sha256-pinned at import (GPL-3.0)",
        font=sans(12),
        fill=INK_FAINT,
    )

    img.save(OUT / "architecture.png", optimize=True)
    print(f"wrote {OUT / 'architecture.png'}  {img.size}")


if __name__ == "__main__":
    build_hero()
    build_architecture()


# ==========================================================================
# silent corruption
# ==========================================================================


def build_corruption() -> None:
    """Draw the defect this package exists to prevent.

    Left: what upstream writes for ``crop left=0.6 right=0.6`` - a MediaBox whose
    right edge sits left of its left edge.  Right: what the API returns instead.
    The numbers are the ones measured from a real run.
    """
    W, H = 1200, 470
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 5], fill=TERRA)

    d.text((56, 36), "THE DEFECT THIS PACKAGE PREVENTS", font=mono_bold(15), fill=TERRA)
    d.text((56, 62), "A broken page, written without an error.",
           font=sans_bold(28), fill=ESPRESSO)
    d.text(
        (56, 106),
        'crop: {"left": 0.6, "right": 0.6}  —  fractions are per side, so this removes 120% of the width.',
        font=sans(15),
        fill=INK_SOFT,
    )

    # -- left: upstream ----------------------------------------------------
    lx, ly, lw, lh = 56, 152, 520, 286
    rounded(d, [lx, ly, lx + lw, ly + lh], 12, fill=PANEL, outline=LINE, width=1)
    d.rectangle([lx, ly + 14, lx + 4, ly + 40], fill=TERRA)
    d.text((lx + 20, ly + 14), "UPSTREAM", font=mono_bold(14), fill=TERRA)
    d.text((lx + 20, ly + 38), "writes the page, reports nothing",
           font=sans(13), fill=INK_FAINT)

    # Page glyph.  The right edge is drawn to the LEFT of the left edge, which is
    # exactly what a negative-width MediaBox looks like when rendered.
    gx, gy, gw, gh = lx + 76, ly + 82, 168, 150
    d.rectangle([gx, gy, gx + gw, gy + gh], fill=(247, 244, 240), outline=LINE)
    d.line([(gx, gy), (gx, gy + gh)], fill=TERRA, width=3)
    d.line([(gx - 40, gy), (gx - 40, gy + gh)], fill=TERRA, width=3)
    # arrow pointing left: "width" is measured backwards
    mid = gy + gh // 2
    d.line([(gx - 4, mid), (gx - 36, mid)], fill=TERRA, width=2)
    d.polygon([(gx - 40, mid), (gx - 28, mid - 6), (gx - 28, mid + 6)], fill=TERRA)
    d.text((gx - 40, gy - 22), "width < 0", font=mono_bold(12), fill=TERRA)
    d.text((gx + 14, gy + 10), "blank in every viewer", font=sans(12), fill=INK_FAINT)
    d.text((gx - 40, gy + gh + 10), "right edge", font=sans(11), fill=INK_FAINT)
    d.text((gx + 60, gy + gh + 10), "left edge", font=sans(11), fill=INK_FAINT)

    d.text((lx + 300, ly + 82), "MediaBox", font=mono(13), fill=INK_SOFT)
    d.text((lx + 300, ly + 104), "[367.2, 0,", font=mono(14), fill=TERRA)
    d.text((lx + 300, ly + 124), " 244.8, 792]", font=mono(14), fill=TERRA)
    d.text((lx + 300, ly + 156), "right edge < left edge", font=sans(12), fill=INK_FAINT)
    d.text((lx + 300, ly + 178), "page renders blank", font=sans(12), fill=INK_FAINT)
    d.text((lx + 300, ly + 206), "exit code 0", font=mono_bold(12), fill=SAGE)
    d.text((lx + 300, ly + 226), "no warning, no dialog", font=sans(12), fill=INK_FAINT)

    # -- right: this package ------------------------------------------------
    rx = 612
    rounded(d, [rx, ly, rx + 532, ly + lh], 12, fill=SAGE_SOFT, outline=(196, 214, 194), width=1)
    d.rectangle([rx, ly + 14, rx + 4, ly + 40], fill=SAGE)
    d.text((rx + 20, ly + 14), "PDFARRANGER-API", font=mono_bold(14), fill=SAGE)
    d.text((rx + 20, ly + 38), "refuses before writing anything",
           font=sans(13), fill=INK_FAINT)

    code = [
        ('{ "ok": false,', INK_SOFT),
        ('  "error": {', INK_SOFT),
        ('    "code": "CROP_OUT_OF_RANGE",', TERRA),
        ('    "message": "crop on page 1 removes', INK_SOFT),
        ('      1.200 of the horizontal axis",', INK_SOFT),
        ('    "details": { "axis": "horizontal",', INK_SOFT),
        ('                "total": 1.2 }', INK_SOFT),
        ('  } }', INK_SOFT),
    ]
    cy = ly + 74
    for line, colour in code:
        d.text((rx + 24, cy), line, font=mono(13), fill=colour)
        cy += 21

    d.line([(rx + 20, cy + 10), (rx + 512, cy + 10)], fill=(196, 214, 194))
    d.text((rx + 24, cy + 22), "exit code 2", font=mono_bold(12), fill=TERRA)
    d.text((rx + 24, cy + 44), "and no file is created", font=sans(12), fill=INK_FAINT)

    img.save(OUT / "corruption.png", optimize=True)
    print(f"wrote {OUT / 'corruption.png'}  {img.size}")
