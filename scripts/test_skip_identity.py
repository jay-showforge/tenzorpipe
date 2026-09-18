#!/usr/bin/env python3
"""Non-reference skip gate: new binary vs the v0.2.0 release binary, byte for byte.

Successful artifacts must be byte-identical after replacing the new engine version string
(read from Cargo.toml, embedded exactly twice) with "0.2.0". Failures must agree on exit
status and the first error line.

    python3 scripts/test_skip_identity.py [media ...]
        (default: every committed fixture + $TENZOR_GEN_FIXTURES from gen_skip_fixtures.sh)

Environment: OLD (default reference/bin/tenzor-v0.2.0-linux-x86_64), NEW (default target/release/tenzor),
RESULT (JSON path), JOBS (parallel cases, default 4).
"""
import concurrent.futures
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD = os.environ.get("OLD", str(ROOT / "reference/bin/tenzor-v0.2.0-linux-x86_64"))
NEW_VERSION = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"].encode()
OLD_VERSION = b"0.2.0"
NEW = os.environ.get("NEW", str(ROOT / "target/release/tenzor"))
GEN = pathlib.Path(os.environ.get("TENZOR_GEN_FIXTURES", "/tmp/tenzor-skip-fixtures"))
# Disk, not tmpfs: long-330 at 0.05 s windows writes ~4 GiB per artifact.
OUT_ROOT = pathlib.Path(os.environ.get("TENZOR_OUTPUT_ROOT", "/tmp/tenzor-artifacts"))
OUT_ROOT.mkdir(parents=True, exist_ok=True)
OUT = pathlib.Path(tempfile.mkdtemp(prefix="skip-identity-", dir=OUT_ROOT))

SETTINGS = [
    [],
    ["--resolution", "160", "--window-sec", "0.33", "--batch-epochs", "2"],
    ["--window-sec", "0.05", "--batch-epochs", "7"],
    ["--execution", "sequential", "--window-sec", "1.0"],
]
# (label, new-binary args, equivalent old-binary args). Old default is 1 worker, new default is auto.
VARIANTS = [
    ("default", [], ["--video-workers", "1"]),
    ("w1", ["--video-workers", "1"], ["--video-workers", "1"]),
    ("w2-chunk250", ["--video-workers", "2", "--chunk-target-ms", "250"], ["--video-workers", "2", "--chunk-target-ms", "250"]),
    ("w8", ["--video-workers", "8"], ["--video-workers", "8"]),
    ("w1-noskip", ["--video-workers", "1", "--no-skip-nonref"], ["--video-workers", "1"]),
    ("w4-noskip", ["--video-workers", "4", "--no-skip-nonref"], ["--video-workers", "4"]),
]


# Error texts this release deliberately rewrote, and the fixtures whose acceptance this
# release deliberately changed. A case only passes through here when the old binary's
# message starts with the recorded text and the new one contains every recorded marker,
# so an unrelated regression still fails the gate. Successful artifacts are never
# excused: they must stay byte-identical.
EXPECTED_ERROR_CHANGES = [
    # (old message prefix, markers required in the new message)
    ("Error: no supported H.264 video track found", ("video track is", "ffmpeg")),
    ("Error: unsupported extension", ("TenzorPipe reads", "ffmpeg")),
    ("Error: MP4 audio codec unsupported", ("audio track is", "ffmpeg")),
    ("Error: only AAC-LC supported", ("HE-AAC", "ffmpeg")),
    ("Error: audio ended before declared duration", ("--audio-tail-tolerance-ms", "ffmpeg")),
]
# Fixtures whose audio ends before its declared duration: within the default tolerance the
# new engine converts them (padding with silence) where the old binary failed.
EXPECTED_NEW_SUCCESS = ("audio-tail-short.mp4",)


def expected_message_change(old_error, new_error):
    return any(
        old_error.startswith(prefix) and all(marker in new_error for marker in markers)
        for prefix, markers in EXPECTED_ERROR_CHANGES
    )


def substituted_sha256(path, substitute):
    """SHA-256 of a file, optionally with NEW_VERSION replaced by OLD_VERSION, in bounded memory.

    Artifacts reach several GiB (330 s at 0.05 s windows), so hash in chunks and carry the last
    len-1 bytes forward so a version string split across a chunk boundary is still found.
    """
    assert len(NEW_VERSION) == len(OLD_VERSION)
    keep = len(NEW_VERSION) - 1
    digest, tail, count = hashlib.sha256(), b"", 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            data = tail + chunk
            if substitute:
                count += data.count(NEW_VERSION)
                data = data.replace(NEW_VERSION, OLD_VERSION)
            digest.update(data[:-keep])
            tail = data[-keep:]
    digest.update(tail)
    if substitute and count != 2:
        raise AssertionError(f"expected 2 version strings in {path}, found {count}")
    return digest.hexdigest()


def run(binary, media, args, tag):
    out = OUT / f"{tag}.tenzor"
    out.unlink(missing_ok=True)
    try:
        p = subprocess.run([binary, "-i", str(media), "-o", str(out), *args], capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"rc": "TIMEOUT", "sha256": None, "error": "", "skipped": None, "panic": False, "leftover": False}
    digest = None
    if p.returncode == 0:
        digest = substituted_sha256(out, binary == NEW and NEW_VERSION != OLD_VERSION)
    leftover = p.returncode != 0 and out.exists()
    out.unlink(missing_ok=True)
    err = next((l for l in p.stderr.splitlines() if l.startswith("Error")), "")
    skipped = re.search(r"skipped_nonref=(\d+)", p.stderr)
    return {"rc": p.returncode, "sha256": digest, "error": err, "panic": "panicked" in p.stderr,
            "leftover": leftover, "skipped": int(skipped.group(1)) if skipped else None}


def case(media, s_index, variant):
    label, new_args, old_args = variant
    settings = SETTINGS[s_index]
    tag = f"{media.stem}-{s_index}-{label}"
    old = run(OLD, media, settings + old_args, tag + "-old")
    new = run(NEW, media, settings + new_args, tag + "-new")
    same_success = old["rc"] == 0 and new["rc"] == 0 and old["sha256"] == new["sha256"]
    same_failure = old["rc"] != 0 and new["rc"] == old["rc"] and new["error"] == old["error"]
    reworded = (
        old["rc"] != 0
        and new["rc"] == old["rc"]
        and expected_message_change(old["error"], new["error"])
    )
    newly_accepted = (
        old["rc"] != 0 and new["rc"] == 0 and media.name in EXPECTED_NEW_SUCCESS
    )
    # Resource exhaustion in the test environment proves nothing about identity.
    infra = any(word in old["error"] + new["error"] for word in ("No space left", "Cannot allocate"))
    ok = (same_success or same_failure or reworded or newly_accepted) and not (
        new["panic"] or new["leftover"] or infra
    )
    kind = (
        "INFRASTRUCTURE" if infra
        else "identical" if same_success
        else "same-error" if same_failure
        else "expected-message-change" if reworded
        else "expected-new-success" if newly_accepted
        else "new-succeeds-where-old-failed" if old["rc"] != 0 and new["rc"] == 0
        else "MISMATCH"
    )
    return dict(file=str(media), settings=" ".join(settings), variant=label, ok=ok, kind=kind,
                old_rc=old["rc"], new_rc=new["rc"], old_error=old["error"], new_error=new["error"],
                skipped_nonref=new["skipped"])


def main():
    if len(sys.argv) > 1:
        media = [pathlib.Path(m) for m in sys.argv[1:]]
    else:
        media = sorted(p for p in (ROOT / "fixtures").iterdir() if p.is_file())
        media += sorted(p for p in (ROOT / "fixtures").glob("audio-v020/*") if p.is_file())
        media += sorted(GEN.glob("*.mp4")) if GEN.exists() else []
    jobs = [(m, s, v) for m in media for s in range(len(SETTINGS)) for v in VARIANTS]
    print(f"{len(media)} media x {len(SETTINGS)} settings x {len(VARIANTS)} variants = {len(jobs)} cases "
          f"(OLD={OLD}, NEW={NEW})", flush=True)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(int(os.environ.get("JOBS", "4"))) as pool:
        for row in pool.map(lambda j: case(*j), jobs):
            rows.append(row)
            if not row["ok"] or row["kind"] != "identical":
                print(("PASS " if row["ok"] else "FAIL ") + row["kind"], pathlib.Path(row["file"]).name,
                      f"[{row['settings']}]", row["variant"], "rc", row["old_rc"], row["new_rc"],
                      row["old_error"][:70], "|", row["new_error"][:70], flush=True)
    summary = {}
    for r in rows:
        summary[r["kind"]] = summary.get(r["kind"], 0) + 1
    skipped_total = sum(r["skipped_nonref"] or 0 for r in rows)
    exercised = sum(1 for r in rows if (r["skipped_nonref"] or 0) > 0 and r["kind"] == "identical")
    fails = sum(not r["ok"] for r in rows)
    result = dict(old_binary=OLD, new_binary=NEW,
                  old_sha256=hashlib.sha256(pathlib.Path(OLD).read_bytes()).hexdigest(),
                  new_sha256=hashlib.sha256(pathlib.Path(NEW).read_bytes()).hexdigest(),
                  cases=len(rows), failures=fails, kinds=summary,
                  identical_cases_with_skipping=exercised, skipped_access_units_total=skipped_total, rows=rows)
    path = pathlib.Path(os.environ.get("RESULT", ROOT / "evidence/v0.3.0/skip-identity.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1))
    print(f"TOTAL {len(rows)} FAIL {fails} kinds={summary} identical-with-skips={exercised} "
          f"skipped-access-units={skipped_total}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
