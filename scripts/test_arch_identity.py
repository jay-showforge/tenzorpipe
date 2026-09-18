#!/usr/bin/env python3
"""Cross-architecture determinism gate: the same input must give the same bytes everywhere.

The engine promises bit-identical tensors. That promise spans machines, so the digests
are recorded once (on the reference architecture) and checked on every other one — an
ARM64 runner, an Apple Silicon laptop, a Jetson.

    python3 scripts/test_arch_identity.py --record    # write evidence/arch-digests.json
    python3 scripts/test_arch_identity.py             # verify this machine against it

Records the host architecture, the fixture digests, and the decode settings used. A
digest mismatch means the two machines disagree about a tensor, which is a bug, not a
tolerance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import platform
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = ROOT / "target/release/tenzor"
RECORD = ROOT / "evidence/arch-digests.json"
# (fixture, extra arguments). Every codec path, both decode modes and the audio-only
# path, so an architecture difference anywhere shows up here.
CASES = [
    ("fixture-h264-aac.mp4", []),
    ("fixture-h264-aac.mp4", ["--video-workers", "1"]),
    ("fixture-h264-aac.mp4", ["--video-workers", "4", "--chunk-target-ms", "250"]),
    ("fixture-h264-aac.mp4", ["--no-skip-nonref"]),
    ("high-bframes.mp4", ["--resolution", "160", "--window-sec", "0.33"]),
    ("baseline720.mp4", []),
    ("color709.mp4", []),
    ("fullrange.mp4", []),
    ("selection-vfr.mp4", []),
    ("selection-sparse.mp4", []),
    ("sync-pulse.mp4", []),
    ("aac-6ch.mp4", []),
    ("audio-only.mp4", []),
    ("wav-44100-2ch.wav", []),
    ("wav-48000-6ch.wav", ["--window-sec", "0.05", "--batch-epochs", "4"]),
    ("hevc-av.mp4", []),
    ("hevc-av.mp4", ["--video-workers", "1"]),
    ("hevc-av.mp4", ["--video-workers", "4", "--chunk-target-ms", "500"]),
    ("hevc-av.mp4", ["--no-skip-nonref"]),
    ("hevc-720.mp4", []),
    ("hevc-bt709.mp4", ["--execution", "sequential"]),
]


def digest(fixture: str, extra: list[str], out_dir: pathlib.Path) -> str:
    source = ROOT / "fixtures" / fixture
    if not source.exists():
        return "missing-fixture"  # reported as a skip, never as a difference
    out = out_dir / "case.tenzor"
    out.unlink(missing_ok=True)
    proc = subprocess.run(
        [str(BINARY), "-i", str(source), "-o", str(out), "-q", *extra],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        return f"error: {proc.stderr.strip().splitlines()[:1]}"
    value = hashlib.sha256(out.read_bytes()).hexdigest()
    out.unlink(missing_ok=True)
    return value


def host() -> dict:
    return {
        "machine": platform.machine(),
        "system": platform.system(),
        "python": platform.python_version(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true", help="write the reference digests")
    args = parser.parse_args()
    if not BINARY.exists():
        raise SystemExit(f"missing {BINARY}; build the release binary first")
    out_dir = pathlib.Path(tempfile.mkdtemp(prefix="arch-identity-"))
    results = {f"{fixture} {' '.join(extra)}".strip(): digest(fixture, extra, out_dir) for fixture, extra in CASES}

    if args.record:
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps({"recorded_on": host(), "digests": results}, indent=1) + "\n")
        print(f"recorded {len(results)} digests from {host()['machine']} into {RECORD.relative_to(ROOT)}")
        for key, value in results.items():
            print(f"  {value[:16]}  {key}")
        return 0

    if not RECORD.exists():
        raise SystemExit(f"missing {RECORD}; run with --record on the reference machine first")
    reference = json.loads(RECORD.read_text())
    expected = reference["digests"]
    same = missing = different = 0
    for key, value in results.items():
        if value == "missing-fixture":
            print(f"SKIP {key}: fixture not generated on this machine")
            missing += 1
        elif key not in expected:
            print(f"SKIP {key}: not in the recorded set")
            missing += 1
        elif expected[key] == value:
            same += 1
        else:
            different += 1
            print(f"FAIL {key}\n  recorded {expected[key]}\n  here     {value}")
    print(
        f"\n{host()['machine']} vs recorded {reference['recorded_on']['machine']}: "
        f"{same} identical, {different} different, {missing} skipped"
    )
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
