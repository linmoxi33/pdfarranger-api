# Changelog

All notable changes to this project. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Error **codes** are part of the public API: adding one is a minor bump, renaming
or removing one is a major bump. The spec **version** field is bumped only for
breaking changes to the JSON format.

## [0.1.0] — 2026-10-20

First release. Wraps PDF Arranger 1.14.0's page engine (GPL-3.0-or-later, vendored
verbatim) in a headless API, SDK and CLI.

### Added

**Headless engine access**

- `pdfarranger_api._engine` installs a *sentinel* `gi` module only when the real
  PyGObject is absent. The sentinel lets `core.py`'s two module-level GUI classes
  (`PasswordDialog`, `PDFRenderer`) be *defined*, so `import` succeeds without
  GTK — but any real use of a GI symbol raises `GuiUnavailable`. A no-op stub
  would let a future upstream change route work through GTK and fail invisibly;
  this one fails loudly.
- A second sentinel covers `cairo`: `core.py` does a bare `import cairo` at
  module scope for the thumbnail renderer. pycairo is a compiled dependency
  absent on most headless boxes and unused by the export path, so the same
  treatment applies. Found by installing the wheel into a bare virtualenv — the
  development environment happened to have pycairo installed, which hid it.
- `Poppler.get_version()` — the single import-time GUI query the engine makes —
  is answered locally (`"0.0.0-headless"`). Nothing on the export path reads the
  resulting constant.
- Vendored files are pinned by sha256 (`VENDOR_HASHES`) and verified at import.
  A modified vendored file raises `ENGINE_INTEGRITY` instead of producing
  subtly different PDFs.

**Declarative spec (version 1)**

- `inputs[]` with per-file passwords; `pages[]` with `input`/`page`/`rotate`/
  `scale`/`crop`/`blank`/`overlay`/`underlay`; top-level `output`, `metadata`,
  `deterministic`, `output_password`, `split`.
- Page selectors: `3`, `"1-3"`, `"1-3,7"`, `[1,5]`, and `"3-"` (to the end,
  resolved once the document is open — the spec parser itself opens no files).
- One `pages[]` entry may expand to many output pages.
- Unknown fields are rejected, not ignored, so a typo like `"rotation"` cannot
  silently produce an unrotated page.

**Preflight validation (`plan`)**

- Opens every input and validates page indices *before* writing anything, so an
  out-of-range page is reported as `PAGE_OUT_OF_RANGE` naming the offending page
  rather than qpdf's `parsing numeric range for <input file>`.
- Computes output geometry (points and millimetres) without writing, so `plan`
  doubles as the CLI's `--dry-run` output.

**Guards against upstream's silent corruption**

- `CROP_OUT_OF_RANGE` when crop fractions are negative or opposite sides sum to
  ≥ 1.0. Upstream writes a MediaBox whose right edge is left of its left edge —
  a negative-width page that renders blank, with no error.
- `SCALE_OUT_OF_RANGE` for `scale ≤ 0` or `> 100`. Upstream writes a zero-size
  page for `scale=0` and an inverted one for negative scales.
- `ANGLE_INVALID` for rotations that are not multiples of 90.

**Execution (`apply`)**

- Atomic writes: every output goes to a sibling temp file and is moved into
  place with `os.replace`, so a crash leaves either the old file or the complete
  new one — never a truncated one.
- `deterministic: true` yields byte-identical output across runs. pikepdf
  rewrites `xmp:MetadataDate` on every save; the stamp is patched and the file
  re-saved with `static_id`/`deterministic_id`.
- Existing outputs are never replaced unless `overwrite=True` / `--overwrite`.
- An output path that collides with an input is refused (`OUTPUT_SAME_AS_INPUT`).
- Bookmarks are preserved and remapped to the new page order (upstream's
  `rebuild_outlines`).
- Blank pages of arbitrary size can be inserted anywhere in the sequence.
- Output encryption via `output_password`.

**CLI**

- `capabilities`, `inspect`, `plan`, `apply`. Every subcommand emits a single
  JSON object on stdout — success or failure — so callers never parse prose.
- Exit codes: `0` ok, `2` usage, `3` input, `4` refused, `5` environment,
  `6` internal.
- UTF-8 safe output on non-UTF-8 consoles.

**SDK**

- `run`, `dry_run`, `plan`, `apply`, `inspect_pdf`, `merge`, `reorder`,
  `split_pages`, `capabilities`.
- 20 stable error codes via `ApiError.code`; no pikepdf or qpdf exception
  escapes.

**Verification**

- 130 tests: engine bootstrap, spec validation, export semantics, CLI through a
  real subprocess, and equivalence with upstream.
- `tools/run_upstream_suite.py` runs upstream's own 29 golden-file exporter
  tests against the vendored copy — proof the engine is unmodified *and* still
  behaves correctly through the shim.
- `test_equivalence.py` drives upstream's `export()` directly and compares
  measured geometry, so "same engine" is asserted rather than assumed. One test
  documents upstream's negative-width crop output; its pair asserts the API
  rejects the same input.
- `tools/mutation_check.py` injects 12 deliberate defects and requires each to
  be caught. Three initially survived, and each exposed a real gap:
  - `atomic_write` — the crash test replaced `_atomic_export` wholesale, so it
    never reached `os.replace`; fixed by asserting the rename mechanism directly
    (`test_output_lands_via_rename_not_in_place_write`).
  - `deterministic_stamp` — the mutation was a no-op, not a real defect; the
    mutation was rewritten and now fails the determinism test as it should.
  - the cairo sentinel raised `TypeError` instead of `GuiUnavailable`, because
    the shim built a plain class rather than one with a guarded metaclass.
- `tools/verify_vendor.py` prints the pinned hashes for release checks.
- Release check: wheel built, installed into a bare virtualenv with neither `gi`
  nor `cairo`, then driven through a full merge/rotate/crop/blank-page job with
  the output read back and measured.

### Known limitations

- `split` writes one file per page; range splitting means several specs.
- Overlay/underlay accept one page per layer at upstream's default placement;
  arbitrary per-layer offsets are not exposed.
- Metadata covers the six XMP fields PDF Arranger itself writes.
- Not a fork: if upstream changes geometry semantics, this package follows it.
