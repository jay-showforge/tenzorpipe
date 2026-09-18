#!/usr/bin/env python3
"""Build MP4s whose audio ends before the duration their container declares.

Real files do this: container durations are rounded and editors trim tails, so the
decoded AAC frames can fall a fraction of a millisecond short of the declared edit
segment. The engine pads gaps up to --audio-tail-tolerance-ms with silence and
rejects longer ones.

Each fixture is derived from an existing repo fixture by lengthening the audio edit
list's segment duration, which is exactly the mismatch observed in the wild. No
FFmpeg is needed.

    python3 scripts/gen_short_tail_fixtures.py
"""

from __future__ import annotations

import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SOURCE = FIXTURES / "fixture-h264-aac.mp4"
# (output name, milliseconds added to the declared audio duration). The encoder's
# frame padding absorbs the first ~16 ms, so these land just past it.
CASES = [("audio-tail-short.mp4", 17), ("audio-tail-long-gap.mp4", 100)]
CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "edts"}


def boxes(buf: bytearray, start: int, end: int):
    """Yield (type, offset, size) for every box, descending into containers."""
    i = start
    while i + 8 <= end:
        size = struct.unpack(">I", buf[i : i + 4])[0]
        typ = bytes(buf[i + 4 : i + 8]).decode("latin1")
        if size == 0:
            size = end - i
        elif size == 1:
            size = struct.unpack(">Q", buf[i + 8 : i + 16])[0]
        if size < 8 or i + size > end:
            return
        yield typ, i, size
        if typ in CONTAINERS:
            yield from boxes(buf, i + 8, i + size)
        i += size


def audio_trak(data: bytearray) -> tuple[int, int]:
    for typ, off, size in boxes(data, 0, len(data)):
        if typ == "trak" and b"mp4a" in data[off : off + size]:
            return off, size
    raise SystemExit("no AAC track found in the source fixture")


def extend_audio_edit(data: bytearray, add_ms: int) -> tuple[int, int]:
    """Add `add_ms` movie units to the audio track's active edit segment."""
    start, size = audio_trak(data)
    for typ, off, _ in boxes(data, start + 8, start + size):
        if typ != "elst":
            continue
        version = data[off + 8]
        count = struct.unpack(">I", data[off + 12 : off + 16])[0]
        pos = off + 16
        for _ in range(count):
            if version == 0:
                segment = struct.unpack(">I", data[pos : pos + 4])[0]
                media_time = struct.unpack(">i", data[pos + 4 : pos + 8])[0]
                if media_time >= 0:
                    data[pos : pos + 4] = struct.pack(">I", segment + add_ms)
                    return segment, segment + add_ms
                pos += 12
            else:
                segment = struct.unpack(">Q", data[pos : pos + 8])[0]
                media_time = struct.unpack(">q", data[pos + 8 : pos + 16])[0]
                if media_time >= 0:
                    data[pos : pos + 8] = struct.pack(">Q", segment + add_ms)
                    return segment, segment + add_ms
                pos += 20
    raise SystemExit("no active audio edit segment found")


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"missing source fixture: {SOURCE}")
    for name, add_ms in CASES:
        data = bytearray(SOURCE.read_bytes())
        before, after = extend_audio_edit(data, add_ms)
        out = FIXTURES / name
        out.write_bytes(bytes(data))
        print(f"{name}: audio edit segment {before} -> {after} movie units (+{add_ms} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
