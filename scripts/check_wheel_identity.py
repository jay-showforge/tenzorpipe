#!/usr/bin/env python3
"""Check that an installed tenzorpipe wheel produces the same bytes as a native tenzor binary.

Run with the Python of a virtual environment where the wheel under test is installed:

    /path/to/venv/bin/python scripts/check_wheel_identity.py [--binary target/release/tenzor] [media ...]

Default media: the committed fixtures used below plus every MP4 in $TENZOR_GEN_FIXTURES
(scripts/gen_skip_fixtures.sh). Each file is converted with default settings and with a small
resolution, 0.33 s windows and one worker. Exit status is nonzero on any difference.
"""
import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

import tenzorpipe as tp

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ["high-bframes.mp4", "baseline720.mp4", "color709.mp4", "fullrange.mp4",
            "sync-pulse.mp4", "aac-6ch.mp4", "wav-44100-2ch.wav"]
SETTINGS = [{}, {"resolution": 160, "window_sec": 0.33, "batch_epochs": 2, "video_workers": 1}]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", nargs="*", type=pathlib.Path)
    ap.add_argument("--binary", default=str(ROOT / "target/release/tenzor"))
    a = ap.parse_args()
    media = a.media or [ROOT / "fixtures" / f for f in FIXTURES] + sorted(
        pathlib.Path(os.environ.get("TENZOR_GEN_FIXTURES", "/tmp/tenzor-skip-fixtures")).glob("*.mp4"))
    out = pathlib.Path(tempfile.mkdtemp(prefix="wheel-identity-", dir=os.environ.get("TENZOR_OUTPUT_ROOT")))
    same = total = 0
    for m in media:
        for i, settings in enumerate(SETTINGS):
            wheel_out, native_out = out / f"{m.stem}-{i}-wheel.tenzor", out / f"{m.stem}-{i}-native.tenzor"
            tp.ingest(m, wheel_out, **settings)
            flags = [x for key, value in settings.items() for x in ("--" + key.replace("_", "-"), str(value))]
            subprocess.run([a.binary, "-i", str(m), "-o", str(native_out), "--quiet", *flags],
                           check=True, stdout=subprocess.DEVNULL)
            identical = wheel_out.read_bytes() == native_out.read_bytes()
            same += identical
            total += 1
            if not identical:
                print("DIFFERENT:", m, settings)
            wheel_out.unlink()
            native_out.unlink()
    print(f"tenzorpipe {tp.__version__} wheel vs {a.binary}: {same}/{total} byte-identical")
    sys.exit(0 if same == total else 1)


if __name__ == "__main__":
    main()
