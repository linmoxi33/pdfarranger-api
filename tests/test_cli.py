"""CLI tests, run through a real subprocess.

Calling ``main()`` in-process would skip everything that actually breaks in
practice: the console encoding on Windows, the exit code reaching the shell, and
the JSON envelope surviving a pipe.  These tests spawn ``python -m
pdfarranger_api`` and assert on stdout/stderr/returncode.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_cli


def parse(proc) -> dict:
    """Parse the JSON envelope, failing with the real output if it is malformed."""
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"stdout was not JSON (exit {proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )


# --------------------------------------------------------------------------
# capabilities / inspect
# --------------------------------------------------------------------------


def test_capabilities_is_machine_readable():
    proc = run_cli("capabilities")
    assert proc.returncode == 0
    payload = parse(proc)
    assert payload["ok"] is True
    result = payload["result"]
    assert result["headless"] is True
    assert result["requires_gui"] is False
    assert "merge" in result["operations"]
    assert "PAGE_OUT_OF_RANGE" in result["error_codes"]
    assert result["limits"]["max_scale"] > 1


def test_capabilities_reports_the_upstream_provenance():
    payload = parse(run_cli("capabilities"))
    upstream = payload["result"]["upstream"]
    assert upstream["project"] == "pdfarranger"
    assert upstream["license"].startswith("GPL")


def test_inspect_reports_page_count_and_sizes(workdir: Path):
    proc = run_cli("inspect", "basic.pdf", cwd=workdir)
    assert proc.returncode == 0
    result = parse(proc)["result"]
    assert result["page_count"] == 7
    assert len(result["pages"]) == 7
    assert result["pages"][0]["width_pt"] > 0


def test_inspect_multiple_files(workdir: Path):
    proc = run_cli("inspect", "basic.pdf", "outlines.pdf", cwd=workdir)
    assert proc.returncode == 0
    result = parse(proc)["result"]
    assert len(result["files"]) == 2


def test_inspect_missing_file_exits_3(workdir: Path):
    proc = run_cli("inspect", "nope.pdf", cwd=workdir)
    assert proc.returncode == 3
    payload = parse(proc)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INPUT_NOT_FOUND"


def test_inspect_encrypted_without_password_exits_3(workdir: Path):
    proc = run_cli("inspect", "encrypted.pdf", cwd=workdir)
    assert proc.returncode == 3
    assert parse(proc)["error"]["code"] == "INPUT_ENCRYPTED"


def test_inspect_encrypted_with_password(workdir: Path):
    proc = run_cli("inspect", "encrypted.pdf", "--password", "foobar", cwd=workdir)
    assert proc.returncode == 0
    assert parse(proc)["result"]["page_count"] == 2


# --------------------------------------------------------------------------
# plan (dry run)
# --------------------------------------------------------------------------


def test_plan_writes_nothing(workdir: Path):
    spec = workdir / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "inputs": [{"id": "a", "path": "basic.pdf"}],
                "pages": [{"input": "a", "page": "1-3"}],
                "output": "planned.pdf",
            }
        ),
        encoding="utf-8",
    )
    proc = run_cli("plan", "--spec", "spec.json", cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    result = parse(proc)["result"]
    assert result["output_page_count"] == 3
    assert result["outputs"][0].endswith("planned.pdf")
    assert not (workdir / "planned.pdf").exists()


def test_plan_resolves_relative_paths_against_the_spec_file(workdir: Path):
    subdir = workdir / "nested"
    subdir.mkdir()
    (subdir / "spec.json").write_text(
        json.dumps(
            {
                "inputs": [{"id": "a", "path": "../basic.pdf"}],
                "pages": [{"input": "a", "page": 1}],
                "output": "out.pdf",
            }
        ),
        encoding="utf-8",
    )
    proc = run_cli("plan", "--spec", "nested/spec.json", cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    assert parse(proc)["ok"] is True


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def test_apply_writes_the_file(workdir: Path):
    spec = workdir / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "inputs": [{"id": "a", "path": "basic.pdf"}],
                "pages": [{"input": "a", "page": 1}],
                "output": "made.pdf",
            }
        ),
        encoding="utf-8",
    )
    proc = run_cli("apply", "--spec", "spec.json", cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    result = parse(proc)["result"]
    assert result["page_count"] == 1
    assert (workdir / "made.pdf").exists()
    assert result["bytes_written"] > 0


def test_apply_inline_json(workdir: Path):
    spec = json.dumps(
        {
            "inputs": [{"id": "a", "path": "basic.pdf"}],
            "pages": [{"input": "a", "page": "1-2"}],
            "output": "inline.pdf",
        }
    )
    proc = run_cli("apply", "--json", spec, cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    assert (workdir / "inline.pdf").exists()


def test_apply_output_flag_overrides_the_spec(workdir: Path):
    spec = json.dumps(
        {
            "inputs": [{"id": "a", "path": "basic.pdf"}],
            "pages": [{"input": "a", "page": 1}],
            "output": "ignored.pdf",
        }
    )
    proc = run_cli("apply", "--json", spec, "-o", "chosen.pdf", cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    assert (workdir / "chosen.pdf").exists()
    assert not (workdir / "ignored.pdf").exists()


def test_apply_refuses_to_overwrite_without_the_flag(workdir: Path):
    target = workdir / "taken.pdf"
    target.write_bytes(b"KEEP")
    spec = json.dumps(
        {
            "inputs": [{"id": "a", "path": "basic.pdf"}],
            "pages": [{"input": "a", "page": 1}],
            "output": "taken.pdf",
        }
    )
    proc = run_cli("apply", "--json", spec, cwd=workdir)
    assert proc.returncode == 4
    assert parse(proc)["error"]["code"] == "OUTPUT_EXISTS"
    assert target.read_bytes() == b"KEEP"


def test_apply_overwrite_flag_replaces_it(workdir: Path):
    target = workdir / "taken.pdf"
    target.write_bytes(b"KEEP")
    spec = json.dumps(
        {
            "inputs": [{"id": "a", "path": "basic.pdf"}],
            "pages": [{"input": "a", "page": 1}],
            "output": "taken.pdf",
        }
    )
    proc = run_cli("apply", "--json", spec, "--overwrite", cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    assert target.read_bytes() != b"KEEP"


def test_apply_dry_run_writes_nothing(workdir: Path):
    spec = json.dumps(
        {
            "inputs": [{"id": "a", "path": "basic.pdf"}],
            "pages": [{"input": "a", "page": 1}],
            "output": "dry.pdf",
        }
    )
    proc = run_cli("apply", "--json", spec, "--dry-run", cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    assert parse(proc)["dry_run"] is True
    assert not (workdir / "dry.pdf").exists()


# --------------------------------------------------------------------------
# error contract at the process boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec_fragment,expected_code,expected_exit",
    [
        ({"pages": [{"input": "a", "page": 99}]}, "PAGE_OUT_OF_RANGE", 3),
        ({"pages": [{"input": "a", "page": 1, "crop": {"left": 0.6, "right": 0.6}}]},
         "CROP_OUT_OF_RANGE", 2),
        ({"pages": [{"input": "a", "page": 1, "scale": 0}]},
         "SCALE_OUT_OF_RANGE", 2),
        ({"pages": [{"input": "a", "page": 1, "rotate": 45}]},
         "ANGLE_INVALID", 2),
        ({"pages": [{"input": "a", "page": 1, "bogus": 1}]},
         "SPEC_UNKNOWN_FIELD", 2),
        ({"inputs": [{"id": "a", "path": "missing.pdf"}]},
         "INPUT_NOT_FOUND", 3),
    ],
)
def test_error_codes_reach_the_caller(
    workdir: Path, spec_fragment, expected_code, expected_exit
):
    spec = {
        "inputs": [{"id": "a", "path": "basic.pdf"}],
        "pages": [{"input": "a", "page": 1}],
        "output": "out.pdf",
    }
    spec.update(spec_fragment)
    proc = run_cli("apply", "--json", json.dumps(spec), cwd=workdir)
    assert proc.returncode == expected_exit, proc.stdout + proc.stderr
    payload = parse(proc)
    assert payload["ok"] is False
    assert payload["error"]["code"] == expected_code
    assert payload["error"]["message"]
    assert payload["error"]["exit_code"] == expected_exit


def test_malformed_json_exits_2_with_a_position(workdir: Path):
    proc = run_cli("apply", "--json", "{not json", cwd=workdir)
    assert proc.returncode == 2
    error = parse(proc)["error"]
    assert error["code"] == "SPEC_INVALID"
    assert "line" in error["details"]


def test_missing_spec_argument_exits_2():
    proc = run_cli("apply")
    assert proc.returncode == 2
    assert parse(proc)["error"]["code"] == "SPEC_INVALID"


def test_error_output_is_valid_json_even_on_failure(workdir: Path):
    """A caller must be able to parse stdout whether the run succeeded or not."""
    proc = run_cli("apply", "--json", json.dumps({
        "inputs": [{"id": "a", "path": "basic.pdf"}],
        "pages": [{"input": "a", "page": 500}],
        "output": "x.pdf",
    }), cwd=workdir)
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)  # must not raise
    assert set(payload) >= {"ok", "command", "error"}


def test_unicode_in_output_survives_the_console(workdir: Path):
    """Windows consoles are not UTF-8 by default; the JSON must still be intact."""
    spec = json.dumps(
        {
            "inputs": [{"id": "a", "path": "basic.pdf"}],
            "pages": [{"input": "a", "page": 1}],
            "output": "unicode.pdf",
            "metadata": {"title": "中文标题 · 测试"},
        },
        ensure_ascii=False,
    )
    proc = run_cli("apply", "--json", spec, cwd=workdir)
    assert proc.returncode == 0, proc.stderr
    assert "中文标题" in proc.stdout or "unicode.pdf" in proc.stdout


def test_version_flag():
    proc = run_cli("--version")
    assert proc.returncode == 0
    assert "pdfarranger-api" in proc.stdout


def test_help_is_available():
    proc = run_cli("--help")
    assert proc.returncode == 0
    assert "capabilities" in proc.stdout
    assert "apply" in proc.stdout


def test_no_subcommand_exits_2():
    proc = run_cli()
    assert proc.returncode == 2
