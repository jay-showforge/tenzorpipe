#!/usr/bin/env python3
"""End-to-end checks for the short-audio-tail policy and the codec guidance messages.

    python3 scripts/gen_short_tail_fixtures.py
    python3 scripts/test_audio_tail_tolerance.py

Optional: --old-binary PATH also asserts that files which already converted produce
byte-identical output, so the tolerance changes nothing for them.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BINARY = ROOT / "target/release/tenzor"
SHORT = FIXTURES / "audio-tail-short.mp4"
LONG_GAP = FIXTURES / "audio-tail-long-gap.mp4"
# Committed fixtures whose audio access units stop early (scripts/gen_truncated_pts_fixture.py).
TRUNCATED = FIXTURES / "audio-truncated-pts.mp4"
TRUNCATED_WIDE = FIXTURES / "audio-truncated-pts-wide.mp4"
OUT = pathlib.Path(tempfile.mkdtemp(prefix="tail-tolerance-"))
RESULTS: list[tuple[str, bool]] = []


def run(binary: pathlib.Path, source: pathlib.Path, *extra: str):
    out = OUT / "case.tenzor"
    out.unlink(missing_ok=True)
    proc = subprocess.run(
        [str(binary), "-i", str(source), "-o", str(out), *extra],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "RUST_BACKTRACE": "0"},
    )
    digest = None
    if proc.returncode == 0:
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
    leftover = out.exists() and proc.returncode != 0
    out.unlink(missing_ok=True)
    return proc.returncode, digest, proc.stderr, leftover


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}{(' :: ' + detail) if detail and not ok else ''}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-binary", type=pathlib.Path)
    args = parser.parse_args()
    for path in (BINARY, SHORT, LONG_GAP):
        if not path.exists():
            raise SystemExit(f"missing {path}; build the release binary and generate fixtures first")

    # A sub-millisecond gap is padded with silence and reported once.
    code, digest, err, _ = run(BINARY, SHORT)
    check("short tail converts", code == 0 and digest is not None, err)
    check("short tail is reported", "padded with silence" in err, err)

    # Quiet mode stays quiet, and the output does not depend on the note.
    quiet_code, quiet_digest, quiet_err, _ = run(BINARY, SHORT, "-q")
    check("quiet suppresses the note", quiet_code == 0 and "padded" not in quiet_err, quiet_err)
    check("quiet output is identical", quiet_digest == digest)

    # Tolerance 0 restores the strict check.
    code, _, err, leftover = run(BINARY, SHORT, "--audio-tail-tolerance-ms", "0")
    check(
        "tolerance 0 rejects any gap",
        code != 0 and not leftover and "--audio-tail-tolerance-ms" in err,
        err,
    )

    # A large gap is rejected by default, with actionable guidance.
    code, _, err, leftover = run(BINARY, LONG_GAP)
    check("large gap is rejected", code != 0 and not leftover, err)
    check(
        "rejection explains the fix",
        all(text in err for text in ("--audio-tail-tolerance-ms", "ffmpeg", "ms")),
        err,
    )
    check("rejection reports both sample counts", "of" in err and "Hz" in err, err)

    # ...and accepted when the operator asks for it.
    code, digest, err, _ = run(BINARY, LONG_GAP, "--audio-tail-tolerance-ms", "100")
    check("explicit tolerance accepts the gap", code == 0 and digest is not None, err)

    # The truncated-PTS fixtures. Their audio *packets* stop early — nothing was
    # stretched — so these are the cases that prove strict mode fires on media an
    # encoder would never produce padding for. They are committed, not derived, so
    # this runs everywhere regardless of the local FFmpeg's muxing behaviour.
    for path in (TRUNCATED, TRUNCATED_WIDE):
        check(f"{path.name} is committed", path.exists())
    if TRUNCATED.exists() and TRUNCATED_WIDE.exists():
        code, digest, err, _ = run(BINARY, TRUNCATED)
        check("truncated PTS converts by default", code == 0 and digest is not None, err)
        check("truncated PTS gap is reported", "5.333 ms" in err and "padded" in err, err)

        code, _, err, leftover = run(BINARY, TRUNCATED, "--audio-tail-tolerance-ms", "0")
        check("strict mode exits 1 on truncated PTS", code == 1, f"exit {code}: {err}")
        check("strict mode writes no partial output", not leftover)
        check(
            "strict rejection names the gap and the option",
            all(t in err for t in ("287744 of 288000", "--audio-tail-tolerance-ms", "48000 Hz")),
            err,
        )

        # A wider truncation is past the default tolerance, so it fails without asking.
        code, _, err, leftover = run(BINARY, TRUNCATED_WIDE)
        check("wide truncation exits 1 by default", code == 1 and not leftover, f"{code}: {err}")
        check("wide truncation reports 90.667 ms", "90.667 ms" in err, err)
        code, digest, err, _ = run(BINARY, TRUNCATED_WIDE, "--audio-tail-tolerance-ms", "100")
        check("wide truncation accepted at 100 ms", code == 0 and digest is not None, err)

        # Strict mode must not reject files that are actually complete.
        code, _, err, _ = run(BINARY, FIXTURES / "fixture-h264-aac.mp4", "--audio-tail-tolerance-ms", "0")
        check("strict mode accepts the untruncated source", code == 0, err)

        # The committed bytes must still be the ones the generator produces.
        gen = subprocess.run(
            [sys.executable, str(ROOT / "scripts/gen_truncated_pts_fixture.py"), "--verify"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        check("committed fixtures match the generator", gen.returncode == 0, gen.stdout + gen.stderr)

    # Option validation.
    code, _, err, _ = run(BINARY, SHORT, "--audio-tail-tolerance-ms", "1001")
    check("tolerance range is validated", code != 0 and "0..1000" in err, err)

    # Unsupported inputs name the codec and the conversion command.
    for source, expected in [
        (FIXTURES / "unsupported-mpeg4.mp4", ("MPEG-4 Part 2", "libx264")),
        (FIXTURES / "unsupported.mp3", (".wav", "ffmpeg")),
    ]:
        if not source.exists():
            continue
        code, _, err, _ = run(BINARY, source)
        check(
            f"{source.name} explains what to do",
            code != 0 and all(text in err for text in expected),
            err,
        )

    # Files that already converted must be unaffected.
    if args.old_binary:
        unchanged = 0
        for source in sorted(FIXTURES.glob("*.*")):
            if source.name.startswith(("audio-tail-", "audio-truncated-")):
                continue  # deliberately short tails; the old binary predates the policy
            for extra in ([], ["--video-workers", "1"], ["--execution", "sequential"]):
                old = run(args.old_binary, source, "-q", *extra)
                new = run(BINARY, source, "-q", *extra)
                if (old[0], old[1]) != (new[0], new[1]):
                    check(f"identical output for {source.name} {' '.join(extra)}", False, new[2])
                else:
                    unchanged += 1
        check(f"{unchanged} existing cases byte-identical", True)

    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
