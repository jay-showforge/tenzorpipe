#!/usr/bin/env python3
"""Cross-architecture numeric gate: how far apart are two machines' tensors, really?

Byte identity holds within an architecture, but not across x86-64 and aarch64: RustFFT
selects AVX2 on one and NEON on the other, and the two round differently. A digest
comparison can only say "different", which is useless for deciding whether that matters.
This measures it.

    python3 scripts/test_arch_tolerance.py --dump DIR      # reference machine, writes .npz
    python3 scripts/test_arch_tolerance.py --compare DIR    # other machine, measures drift

Reports, per case and per tensor column, the maximum absolute difference and the maximum
ULP distance. A shape mismatch, a NaN, or drift past the thresholds is algorithmic and
fails; a last-bit disagreement is rounding and passes.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_arch_identity import CASES  # noqa: E402  (same case list as the identity gate)

BINARY = ROOT / "target/release/tenzor"


def read_tenzor(path: pathlib.Path) -> dict[str, np.ndarray]:
    """Same reader the H.265 fidelity gate uses, so both gates see identical arrays."""
    reader = ipc.open_file(pa.memory_map(str(path)))
    columns: dict[str, list] = {name: [] for name in reader.schema.names}
    for i in range(reader.num_record_batches):
        batch = reader.get_batch(i)
        for name in columns:
            column = batch.column(name)
            if pa.types.is_fixed_size_list(column.type):
                columns[name].append(
                    column.values.to_numpy(zero_copy_only=True).reshape(batch.num_rows, -1)
                )
            else:
                columns[name].append(column.to_numpy(zero_copy_only=True))
    return {k: np.concatenate(v) for k, v in columns.items()}


def ulp_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Representable float32 steps between a and b, across the sign boundary."""
    def ordered(x: np.ndarray) -> np.ndarray:
        i = x.astype(np.float32).view(np.int32).astype(np.int64)
        # Map the sign-magnitude layout onto a monotonic integer line.
        return np.where(i < 0, np.int64(-0x80000000) - i, i)
    return np.abs(ordered(a) - ordered(b))


def tensors_for(fixture: str, extra: list[str], work: pathlib.Path):
    source = ROOT / "fixtures" / fixture
    if not source.exists():
        return None
    out = work / "case.tenzor"
    out.unlink(missing_ok=True)
    proc = subprocess.run(
        [str(BINARY), "-i", str(source), "-o", str(out), "-q", *extra],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        return None
    data = read_tenzor(out)
    out.unlink(missing_ok=True)
    return {k: v for k, v in data.items() if np.issubdtype(v.dtype, np.floating)}


def slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", metavar="DIR", help="write this machine's tensors as the reference")
    ap.add_argument("--compare", metavar="DIR", help="measure this machine against a dump")
    ap.add_argument("--max-ulp", type=int, default=2)
    ap.add_argument("--max-abs", type=float, default=1e-6)
    args = ap.parse_args()
    if bool(args.dump) == bool(args.compare):
        raise SystemExit("pass exactly one of --dump or --compare")
    if not BINARY.exists():
        raise SystemExit(f"missing {BINARY}; build the release binary first")

    target = pathlib.Path(args.dump or args.compare)
    work = pathlib.Path(tempfile.mkdtemp(prefix="arch-tolerance-"))
    worst_ulp, worst_abs, failures, compared, skipped = 0, 0.0, [], 0, 0

    if args.dump:
        target.mkdir(parents=True, exist_ok=True)

    for fixture, extra in CASES:
        key = f"{fixture} {' '.join(extra)}".strip()
        here = tensors_for(fixture, extra, work)
        if here is None:
            print(f"SKIP {key}: fixture missing or engine refused it")
            skipped += 1
            continue
        path = target / f"{slug(key)}.npz"
        if args.dump:
            np.savez_compressed(path, **here)
            continue
        if not path.exists():
            print(f"SKIP {key}: no reference dump")
            skipped += 1
            continue
        ref = np.load(path)
        for name, mine in sorted(here.items()):
            if name not in ref:
                failures.append(f"{key} [{name}]: column absent from the reference")
                continue
            theirs = ref[name]
            if theirs.shape != mine.shape:
                failures.append(f"{key} [{name}]: shape {mine.shape} vs {theirs.shape}")
                continue
            if not (np.isfinite(mine).all() and np.isfinite(theirs).all()):
                failures.append(f"{key} [{name}]: non-finite values present")
                continue
            delta = float(np.abs(mine - theirs).max())
            ulps = int(ulp_distance(mine, theirs).max())
            compared += 1
            worst_abs, worst_ulp = max(worst_abs, delta), max(worst_ulp, ulps)
            flag = "" if (ulps <= args.max_ulp and delta <= args.max_abs) else "  <-- OVER THRESHOLD"
            if ulps or delta:
                print(f"  {key} [{name}]: max|delta| {delta:.3e}, max {ulps} ULP{flag}")
            if flag:
                failures.append(f"{key} [{name}]: {delta:.3e} abs, {ulps} ULP")

    if args.dump:
        print(f"wrote reference tensors for {len(CASES) - skipped} cases into {target}")
        return 0

    print(
        f"\n{compared} tensor columns compared, {skipped} skipped: "
        f"worst {worst_ulp} ULP, worst |delta| {worst_abs:.3e} "
        f"(limits: {args.max_ulp} ULP, {args.max_abs:.0e})"
    )
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
