#!/usr/bin/env bash
# Build the release wheel and smoke-test it in fresh virtual environments:
#   1. install the wheel without torch: ingest + Arrow read, CLI entry point, error handling
#   2. install the wheel with CPU torch: TenzorDataset tensors have the documented shapes
#   3. `pip install .` from this source tree: output byte-identical to the wheel's
# Requires Rust 1.98.1, NASM and python3. Wheels land in dist/.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
WORK="${WHEEL_WORK:-$HOME/.tenzor-build/wheel}"
PY="${PYTHON:-python3}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cpu}"
CLIP="${SMOKE_CLIP:-$ROOT/fixtures/high-bframes.mp4}"
mkdir -p "$WORK" dist

venv() {  # venv <dir>: create a virtual environment with pip even where ensurepip is missing
  rm -rf "$1"
  if ! "$PY" -m venv "$1" >/dev/null 2>&1; then
    "$PY" -m venv --without-pip "$1"
    [ -f "$WORK/get-pip.py" ] || curl -fsSL -o "$WORK/get-pip.py" https://bootstrap.pypa.io/get-pip.py
    "$1/bin/python" "$WORK/get-pip.py" --quiet
  fi
}

venv "$WORK/build-venv"
"$WORK/build-venv/bin/pip" install --quiet "maturin>=1.15,<2"
rm -f dist/tenzorpipe-*.whl
"$WORK/build-venv/bin/maturin" build --release --out dist
WHEEL=$(ls dist/tenzorpipe-*.whl)
echo "built $WHEEL ($(du -h "$WHEEL" | cut -f1))"
"$WORK/build-venv/bin/python" -m zipfile -l "$WHEEL" | awk '{print $1}' | grep -E "_engine|__init__|LICENSE$|METADATA|entry_points" 

echo "== smoke 1: wheel without torch"
venv "$WORK/smoke-arrow"
"$WORK/smoke-arrow/bin/pip" install --quiet "$WHEEL"
OUT="$WORK/smoke"; rm -rf "$OUT"; mkdir -p "$OUT"
"$WORK/smoke-arrow/bin/python" -c "
import tenzorpipe as tp, pyarrow as pa, pyarrow.ipc as ipc, sys
r = tp.ingest(sys.argv[1], sys.argv[2] + '/a.tenzor')
t = ipc.open_file(pa.memory_map(r['output'])).read_all()
print('version', tp.__version__, '| epochs', r['epochs'], '| rows', t.num_rows, '| columns', t.column_names)
assert t.num_rows == r['epochs'] > 0
try:
    tp.ingest(sys.argv[1], r['output'])
except tp.TenzorError as e:
    print('existing output rejected:', str(e)[:60])
else:
    raise SystemExit('expected TenzorError')
try:
    tp.load(r['output']).get_batch(0)
except ImportError as e:
    print('torch-free install:', e)
" "$CLIP" "$OUT"
"$WORK/smoke-arrow/bin/tenzor" -i "$CLIP" -o "$OUT/cli.tenzor" --video-workers 2
"$WORK/smoke-arrow/bin/tenzor" --version
cmp "$OUT/a.tenzor" "$OUT/cli.tenzor" && echo "python ingest == wheel CLI output (byte-identical)"
if [ -x "$ROOT/target/release/tenzor" ]; then
  "$ROOT/target/release/tenzor" -i "$CLIP" -o "$OUT/bin.tenzor" 2>/dev/null
  cmp "$OUT/a.tenzor" "$OUT/bin.tenzor" && echo "python ingest == target/release/tenzor output (byte-identical)"
fi

echo "== smoke 2: wheel with CPU torch"
venv "$WORK/smoke-torch"
"$WORK/smoke-torch/bin/pip" install --quiet "$WHEEL"
"$WORK/smoke-torch/bin/pip" install --quiet torch --index-url "$TORCH_INDEX"
"$WORK/smoke-torch/bin/python" -c "
import tenzorpipe as tp, sys, torch
r = tp.ingest(sys.argv[1], sys.argv[2] + '/t.tenzor', resolution=160, window_sec=0.33)
with tp.load(r['output']) as data:
    b = next(data.iter_batches())
    print('video', tuple(b['video'].shape), b['video'].dtype, '| audio', tuple(b['audio'].shape), '| rows', len(data))
    assert b['video'].shape[1:] == (3, 160, 160) and b['audio'].shape[1:] == (33, 64)
    assert torch.isfinite(b['video']).all() and float(b['video'].min()) >= -1 and float(b['video'].max()) <= 1
" "$CLIP" "$OUT"
echo "== smoke 3: pip install . (source build through maturin)"
venv "$WORK/smoke-source"
"$WORK/smoke-source/bin/pip" install --quiet "$ROOT"
"$WORK/smoke-source/bin/python" -c "import tenzorpipe as tp, sys; print('source install', tp.__version__, tp.ingest(sys.argv[1], sys.argv[2] + '/s.tenzor')['epochs'], 'epochs')" "$CLIP" "$OUT"
cmp "$OUT/a.tenzor" "$OUT/s.tenzor" && echo "pip install . output == wheel output (byte-identical)"
echo "WHEEL_SMOKE PASS $WHEEL"
