#!/usr/bin/env python3
"""Build MP4s whose audio *packets* stop before the container's declared duration.

Why this exists
---------------
`--audio-tail-tolerance-ms 0` is the strict setting: reject any file whose audio ends
early rather than pad the gap with silence. Proving that it fires is awkward with
FFmpeg-produced media, because a normal mux reconciles the trailing AAC access units
against the edit list — the encoder's frame padding covers the tail and there is no gap
left to reject. The independent stress test hit exactly that.

So the gap is made here, in the sample table, with no encoder in the loop: trailing AAC
access units are deleted from the audio track's `stts`/`stsz`/`stsc`/`stco` (and its
`sbgp` roll group), while `mdhd` and the edit list keep declaring the original duration.
The audio timeline that remains is continuous and legal — it simply ends before the
video does, which is the condition the strict check exists to catch.

    python3 scripts/gen_truncated_pts_fixture.py            # write the fixtures
    python3 scripts/gen_truncated_pts_fixture.py --verify    # committed bytes still match

The outputs are committed, so CI never depends on FFmpeg's muxing behaviour. `--verify`
regenerates them and compares byte for byte, so the committed blobs stay auditable.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SOURCE = FIXTURES / "fixture-h264-aac.mp4"
CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "edts", "udta"}
# (output name, access units deleted from the end of the audio track).
#
# The source holds 283 AAC frames at 48 kHz; the edit list skips 1024 priming samples
# and declares 288000, leaving 768 samples (16 ms) of encoder padding as slack. So each
# deleted access unit past the first costs a full 1024 samples (21.333 ms) of tail.
#   1 unit -> 256 samples, 5.333 ms: under the 25 ms default, over strict 0.
#   5 units -> 4352 samples, 90.667 ms: over the default, under an explicit 100 ms.
CASES = [("audio-truncated-pts.mp4", 1), ("audio-truncated-pts-wide.mp4", 5)]


def boxes(buf: bytes, start: int, end: int):
    """Yield (type, offset, size, header_size) for each box in [start, end)."""
    i = start
    while i + 8 <= end:
        size = struct.unpack(">I", buf[i : i + 4])[0]
        typ = buf[i + 4 : i + 8].decode("latin1")
        header = 8
        if size == 0:
            size = end - i
        elif size == 1:
            size = struct.unpack(">Q", buf[i + 8 : i + 16])[0]
            header = 16
        if size < header or i + size > end:
            return
        yield typ, i, size, header
        i += size


def find(buf: bytes, start: int, end: int, path: tuple[str, ...]) -> tuple[int, int, int]:
    """Locate a descendant box by its type path, e.g. ("mdia", "minf", "stbl")."""
    head, rest = path[0], path[1:]
    for typ, off, size, header in boxes(buf, start, end):
        if typ != head:
            continue
        if not rest:
            return off, size, header
        return find(buf, off + header, off + size, rest)
    raise SystemExit(f"box {'/'.join(path)} not found")


def audio_trak(buf: bytes) -> tuple[int, int, int]:
    moov_off, moov_size, moov_header = find(buf, 0, len(buf), ("moov",))
    for typ, off, size, header in boxes(buf, moov_off + moov_header, moov_off + moov_size):
        # The audio track is the one whose sample description is an MPEG-4 audio entry.
        if typ == "trak" and b"mp4a" in buf[off:off + size]:
            return off, size, header
    raise SystemExit("no AAC track in the source fixture")


def full_box(buf: bytes, off: int) -> tuple[int, bytes]:
    """Return (payload offset after version/flags, the 12-byte FullBox header)."""
    return off + 12, buf[off + 4 : off + 12]


def read_table(buf: bytes, off: int, fields: int) -> tuple[bytes, list[tuple[int, ...]]]:
    """Read a FullBox holding a u32 entry count followed by fixed-width u32 rows."""
    payload, head = full_box(buf, off)
    count = struct.unpack(">I", buf[payload : payload + 4])[0]
    rows = []
    pos = payload + 4
    for _ in range(count):
        rows.append(struct.unpack(f">{fields}I", buf[pos : pos + 4 * fields]))
        pos += 4 * fields
    return head, rows


def write_table(head: bytes, rows: list[tuple[int, ...]], fields: int) -> bytes:
    body = struct.pack(">I", len(rows)) + b"".join(struct.pack(f">{fields}I", *r) for r in rows)
    return struct.pack(">I", 12 + len(body)) + head + body


def truncate(data: bytes, drop_units: int) -> tuple[bytes, int, int]:
    """Delete `drop_units` trailing access units from the audio track's sample table."""
    trak_off, trak_size, trak_header = audio_trak(data)
    stbl_off, stbl_size, stbl_header = find(
        data, trak_off + trak_header, trak_off + trak_size, ("mdia", "minf", "stbl")
    )

    def child(name: str) -> tuple[int, int]:
        off, size, _ = find(data, stbl_off + stbl_header, stbl_off + stbl_size, (name,))
        return off, size

    stts_off, stts_size = child("stts")
    stsz_off, stsz_size = child("stsz")
    stsc_off, stsc_size = child("stsc")
    stco_off, stco_size = child("stco")

    stts_head, stts_rows = read_table(data, stts_off, 2)
    total = sum(count for count, _ in stts_rows)
    kept = total - drop_units
    if kept < 1:
        raise SystemExit(f"cannot drop {drop_units} of {total} access units")

    # stts: keep whole entries from the front until `kept` samples are covered.
    new_stts, remaining = [], kept
    for count, delta in stts_rows:
        if remaining <= 0:
            break
        take = min(count, remaining)
        new_stts.append((take, delta))
        remaining -= take

    # stsz: variable sample sizes, one u32 per access unit.
    stsz_payload, stsz_head = full_box(data, stsz_off)
    sample_size = struct.unpack(">I", data[stsz_payload : stsz_payload + 4])[0]
    if sample_size != 0:
        raise SystemExit("fixed-size AAC samples are not expected in this fixture")
    sizes = list(
        struct.unpack(f">{total}I", data[stsz_payload + 8 : stsz_payload + 8 + 4 * total])
    )
    new_stsz = (
        struct.pack(">I", 20 + 4 * kept)
        + stsz_head
        + struct.pack(">II", 0, kept)
        + b"".join(struct.pack(">I", s) for s in sizes[:kept])
    )

    # stsc/stco: expand the run-length chunk map, refill it with the kept samples.
    stco_head, stco_rows = read_table(data, stco_off, 1)
    _, stsc_rows = read_table(data, stsc_off, 3)
    per_chunk = []
    for i, (first, spc, desc) in enumerate(stsc_rows):
        last = stsc_rows[i + 1][0] - 1 if i + 1 < len(stsc_rows) else len(stco_rows)
        per_chunk.extend([(spc, desc)] * (last - first + 1))
    filled, remaining = [], kept
    for spc, desc in per_chunk:
        if remaining <= 0:
            break
        take = min(spc, remaining)
        filled.append((take, desc))
        remaining -= take
    if remaining:
        raise SystemExit("chunk map does not cover the kept samples")
    new_stsc = []
    for index, (spc, desc) in enumerate(filled):
        if not new_stsc or new_stsc[-1][1:] != (spc, desc):
            new_stsc.append((index + 1, spc, desc))

    replacements = {
        stts_off: (stts_size, write_table(stts_head, new_stts, 2)),
        stsz_off: (stsz_size, new_stsz),
        stsc_off: (stsc_size, write_table(read_table(data, stsc_off, 3)[0], new_stsc, 3)),
        stco_off: (stco_size, write_table(stco_head, stco_rows[: len(filled)], 1)),
    }

    # sbgp (the AAC roll group) counts samples too; keep it consistent or a strict
    # parser sees a group covering access units that no longer exist.
    try:
        sbgp_off, sbgp_size, _ = find(
            data, stbl_off + stbl_header, stbl_off + stbl_size, ("sbgp",)
        )
    except SystemExit:
        sbgp_off = None
    if sbgp_off is not None:
        head = data[sbgp_off + 4 : sbgp_off + 16]  # version/flags + grouping_type
        count = struct.unpack(">I", data[sbgp_off + 16 : sbgp_off + 20])[0]
        rows, pos = [], sbgp_off + 20
        for _ in range(count):
            rows.append(struct.unpack(">II", data[pos : pos + 8]))
            pos += 8
        new_rows, remaining = [], kept
        for samples, desc in rows:
            if remaining <= 0:
                break
            take = min(samples, remaining)
            new_rows.append((take, desc))
            remaining -= take
        body = struct.pack(">I", len(new_rows)) + b"".join(
            struct.pack(">II", *r) for r in new_rows
        )
        replacements[sbgp_off] = (sbgp_size, struct.pack(">I", 16 + len(body)) + head + body)

    out = bytearray(data)
    delta = 0
    for off in sorted(replacements, reverse=True):
        old_size, new_bytes = replacements[off]
        out[off : off + old_size] = new_bytes
        delta += len(new_bytes) - old_size

    # Every table shrank, so widen nothing: just correct the enclosing box sizes.
    # `moov` sits after `mdat` in this fixture, so no chunk offset moves.
    minf_off, _, _ = find(data, trak_off + trak_header, trak_off + trak_size, ("mdia", "minf"))
    mdia_off, _, _ = find(data, trak_off + trak_header, trak_off + trak_size, ("mdia",))
    moov_off, _, _ = find(data, 0, len(data), ("moov",))
    for off in (stbl_off, minf_off, mdia_off, trak_off, moov_off):
        size = struct.unpack(">I", out[off : off + 4])[0]
        out[off : off + 4] = struct.pack(">I", size + delta)
    return bytes(out), total, kept


def build() -> list[tuple[str, bytes, int, int]]:
    if not SOURCE.exists():
        raise SystemExit(f"missing source fixture: {SOURCE}")
    data = SOURCE.read_bytes()
    return [(name, *truncate(data, drop)) for name, drop in CASES]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="compare with the committed files")
    args = parser.parse_args()
    status = 0
    for name, blob, total, kept in build():
        path = FIXTURES / name
        digest = hashlib.sha256(blob).hexdigest()[:16]
        if args.verify:
            if not path.exists():
                print(f"FAIL {name}: not committed")
                status = 1
            elif path.read_bytes() != blob:
                print(f"FAIL {name}: committed bytes differ from the generated ones")
                status = 1
            else:
                print(f"PASS {name}: {kept} of {total} access units, sha256 {digest}")
        else:
            path.write_bytes(blob)
            print(f"{name}: kept {kept} of {total} audio access units, sha256 {digest}")
    return status


if __name__ == "__main__":
    sys.exit(main())
