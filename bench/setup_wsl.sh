#!/usr/bin/env bash
# One-time environment for bench/gpu_bench.py. Runs as a normal user inside WSL2/Linux:
# no sudo, nothing installed system-wide. Everything lives under $BENCH_HOME.
set -euo pipefail
BENCH_HOME="${BENCH_HOME:-$HOME/.tenzor-bench}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"
DALI_PKG="${DALI_PKG:-nvidia-dali-cuda130}"
FFMPEG_VER="${FFMPEG_VER:-8.1}"
mkdir -p "$BENCH_HOME"
cd "$BENCH_HOME"

# FFmpeg shared build (default 8.1): provides the ffmpeg/ffprobe CLIs and the libav* shared
# libraries TorchCodec links against (including NVDEC/cuvid support).
if [ ! -x ffmpeg/bin/ffmpeg ]; then
  url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n${FFMPEG_VER}-latest-linux64-gpl-shared-${FFMPEG_VER}.tar.xz"
  curl -fL --retry 3 -o ffmpeg.tar.xz "$url"
  mkdir -p ffmpeg && tar -xJf ffmpeg.tar.xz -C ffmpeg --strip-components=1 && rm ffmpeg.tar.xz
fi

# Ubuntu may lack ensurepip (python3-venv) and we avoid sudo: bootstrap pip ourselves.
if [ ! -x venv/bin/pip ]; then
  rm -rf venv && python3 -m venv --without-pip venv
  curl -fsSL -o get-pip.py https://bootstrap.pypa.io/get-pip.py
  venv/bin/python get-pip.py --quiet && rm get-pip.py
fi
. venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch torchvision torchcodec --index-url "$TORCH_INDEX"
python -m pip install --extra-index-url https://pypi.nvidia.com "$DALI_PKG"
python -m pip install numpy pyarrow psutil nvidia-ml-py

cat > env.sh <<ENV
export PATH="$BENCH_HOME/ffmpeg/bin:\$PATH"
export LD_LIBRARY_PATH="$BENCH_HOME/ffmpeg/lib:/usr/lib/wsl/lib:\${LD_LIBRARY_PATH:-}"
. "$BENCH_HOME/venv/bin/activate"
ENV
. ./env.sh
ffmpeg -hide_banner -version | head -1
python - <<'PY'
import torch, torchcodec, nvidia.dali as dali
print("torch", torch.__version__, "cuda", torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0))
print("torchcodec", torchcodec.__version__, "dali", dali.__version__)
PY
