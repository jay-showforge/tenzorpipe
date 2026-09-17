"""Python-facing tests for the installed tenzorpipe package.

Run after `maturin develop` (or `pip install .`):

    pytest tests/                 # fast: package behaviour + recorded regression evidence
    pytest tests/ --run-matrix    # also re-runs the full 1,056-case identity matrix (~13 min)
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tomllib

import pytest

import tenzorpipe as tp

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/v0.3.0/skip-identity.json"
ENGINE = ROOT / "bin/tenzor-linux-x86_64"
EXPECTED_CASES = 1056


def test_version_matches_cargo_manifest():
    cargo = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]
    assert tp.__version__ == cargo


def test_ingest_then_load_round_trip(clip, out_path):
    info = tp.ingest(clip, out_path)
    assert info["epochs"] == 20 and info["seconds"] > 0
    assert out_path.exists()
    with tp.load(out_path) as data:
        assert len(data) == info["epochs"]
        assert data.video_shape == (3, 224, 224)
        assert data.audio_shape == (50, 64)
        rows = 0
        for batch in data.iter_batches():
            assert batch["video"].shape[1:] == (3, 224, 224)
            assert batch["audio"].shape[1:] == (50, 64)
            assert batch["video"].min() >= -1.001 and batch["video"].max() <= 1.001
            rows += batch["video"].shape[0]
        assert rows == info["epochs"]


def test_ingest_options_reach_the_engine(clip, tmp_path):
    info = tp.ingest(clip, tmp_path / "small.tenzor", resolution=64, window_sec=1.0, video_workers=1)
    assert info["epochs"] == 10
    with tp.load(tmp_path / "small.tenzor") as data:
        assert data.video_shape == (3, 64, 64) and data.audio_shape == (100, 64)


def test_profile_returns_stage_timers(clip, out_path):
    info = tp.ingest(clip, out_path, profile=True)
    assert info["profile"]["wall_seconds"] > 0
    assert "video_decode" in info["profile"]["stages_seconds"]


def test_existing_output_is_never_overwritten(clip, out_path):
    tp.ingest(clip, out_path)
    before = out_path.read_bytes()
    with pytest.raises(tp.TenzorError):
        tp.ingest(clip, out_path)
    assert out_path.read_bytes() == before


def test_unreadable_input_raises_and_leaves_no_artifact(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not an mp4")
    out = tmp_path / "broken.tenzor"
    with pytest.raises(tp.TenzorError):
        tp.ingest(bad, out)
    assert not out.exists()


def _ingest_in_subprocess(clip, out, verbose):
    code = (f"import tenzorpipe as tp; tp.ingest({str(clip)!r}, {str(out)!r}, verbose={verbose})")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)


def test_quiet_by_default_and_verbose_on_request(clip, tmp_path):
    quiet = _ingest_in_subprocess(clip, tmp_path / "q.tenzor", False)
    assert quiet.stderr == "", f"expected silence, got: {quiet.stderr!r}"
    loud = _ingest_in_subprocess(clip, tmp_path / "v.tenzor", True)
    assert "video_mode=" in loud.stderr and "skipped_nonref=" in loud.stderr
    # Diagnostics must not change the tensors.
    assert (tmp_path / "q.tenzor").read_bytes() == (tmp_path / "v.tenzor").read_bytes()


def test_console_entry_point_reports_version():
    result = subprocess.run([sys.executable, "-c",
                             "from tenzorpipe import _engine; raise SystemExit(_engine.run_cli(['--version']))"],
                            capture_output=True, text=True)
    assert result.returncode == 0 and "tenzor" in result.stdout


@pytest.mark.skipif(not ENGINE.exists(), reason="bin/tenzor-linux-x86_64 not present")
def test_python_output_matches_the_engine_binary(clip, tmp_path):
    from_python, from_binary = tmp_path / "py.tenzor", tmp_path / "bin.tenzor"
    tp.ingest(clip, from_python, video_workers=2)
    subprocess.run([str(ENGINE), "-i", str(clip), "-o", str(from_binary), "--video-workers", "2", "--quiet"],
                   check=True)
    assert from_python.read_bytes() == from_binary.read_bytes()


@pytest.mark.skipif(not EVIDENCE.exists(), reason="release-gate evidence not present")
def test_recorded_regression_matrix_has_no_drift():
    """The 1,056-case byte-identity matrix, as recorded by scripts/release_gates.sh."""
    data = json.loads(EVIDENCE.read_text())
    assert data["cases"] == EXPECTED_CASES, data["cases"]
    assert data["failures"] == 0, data["kinds"]
    assert data["kinds"].get("identical", 0) + data["kinds"].get("same-error", 0) == EXPECTED_CASES
    assert data["identical_cases_with_skipping"] > 0 and data["skipped_access_units_total"] > 0
    if ENGINE.exists():
        assert hashlib.sha256(ENGINE.read_bytes()).hexdigest() == data["new_sha256"], \
            "bin/tenzor-linux-x86_64 is not the binary the matrix verified"


@pytest.mark.matrix
def test_full_identity_matrix_reruns_clean():
    """Opt-in: actually re-run all 1,056 comparisons against the v0.2.0 oracle."""
    result = subprocess.run([sys.executable, str(ROOT / "scripts/test_skip_identity.py")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-2000:]
    assert f"TOTAL {EXPECTED_CASES} FAIL 0" in result.stdout, result.stdout[-2000:]
