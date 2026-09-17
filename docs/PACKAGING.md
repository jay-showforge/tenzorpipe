# Packaging the `tenzorpipe` wheel

The Python package is one abi3 wheel per platform (CPython 3.9+), built with maturin from
`bindings/python` (PyO3) and `python/tenzorpipe`. This page covers building wheels that install on
older glibc / enterprise Linux, and how to verify them before publishing.

## Which glibc a wheel needs

A wheel built directly on a Linux host links against that host's glibc symbol versions.
`scripts/build_wheel.sh` on the Ubuntu 24.04 (glibc 2.39) release machine produces
`manylinux_2_34`: `auditwheel show` reports `GLIBC_2.32`–`GLIBC_2.34` symbols. The C++ runtime
requirements are old (`GLIBCXX_3.4`, `CXXABI_1.3.9`), so the limit is only the build host.

| Wheel tag | Minimum glibc | Installs on, for example | Build route |
|---|---|---|---|
| `manylinux_2_34` | 2.34 | Ubuntu 22.04+, Debian 12+, RHEL/Alma/Rocky 9+, Amazon Linux 2023 | native: `bash scripts/build_wheel.sh` |
| `manylinux_2_28` | 2.28 | Ubuntu 20.04+, Debian 10+, RHEL/Alma/Rocky 8+ | zig (A) or manylinux_2_28 container (B) |
| `manylinux_2_17` | 2.17 | RHEL/CentOS 7+, Amazon Linux 2 | zig (A) |

Not covered by any of these: musl (Alpine), aarch64, macOS and Windows. The engine is only
tested on Linux x86-64.

pip picks the most compatible wheel it can install, so a single `manylinux_2_17` or `manylinux_2_28`
wheel on PyPI serves every newer distribution as well.

## A. Zig cross-linking (no container needed)

maturin can link against an older glibc through `zig cc`. OpenH264's C++ is then compiled with
zig's clang and libc++, which is statically linked into the extension.

```sh
# Inside the repository on Linux x86-64, with Rust 1.98.1 and NASM installed
COMPATIBILITY=manylinux_2_28 bash scripts/build_wheel.sh      # or manylinux_2_17
```

That installs `ziglang` into the build venv, runs
`maturin build --release --zig --compatibility $COMPATIBILITY --target x86_64-unknown-linux-gnu`,
and runs the same smoke tests as the default build.

Verified on the release machine (2026-09-16), for both `manylinux_2_28` and `manylinux_2_17`:

- `auditwheel show` confirms each tag.
- `scripts/check_wheel_identity.py` reports **44/44 byte-identical** outputs against the native
  `tenzor` binary (22 media including 1080p, B-pyramid, VFR, open-GOP, 23.976 fps and WAV inputs,
  each at two settings).

Not yet verified: installing and running these wheels **on** an old distribution (step 3 of the
checklist below). Do that before publishing.

## B. manylinux container (Docker or Podman)

The PyPA images provide the old glibc and a compatible GCC toolset. The image lacks NASM,
which OpenH264 requires (the vendored build fails without it), so build it first. Rust 1.98.1 is
installed automatically from `rust-toolchain.toml`.

```sh
docker run --rm -v "$PWD":/io -w /io quay.io/pypa/manylinux_2_28_x86_64 bash -euxc '
  curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain none
  . "$HOME/.cargo/env"
  curl -fsSL https://www.nasm.us/pub/nasm/releasebuilds/2.16.01/nasm-2.16.01.tar.xz | tar -xJ -C /tmp
  (cd /tmp/nasm-2.16.01 && ./configure --prefix=/usr/local && make -j"$(nproc)" && make install)
  PY=/opt/python/cp312-cp312/bin
  "$PY/pip" install "maturin>=1.15,<2"
  CARGO_TARGET_DIR=/tmp/target "$PY/maturin" build --release --compatibility manylinux_2_28 \
    --interpreter "$PY/python" --out /io/dist-manylinux
  chown -R "$(stat -c %u:%g /io)" /io/dist-manylinux
'
```

- The abi3 wheel needs only one interpreter; `cp312` is simply one present in the image.
- Keep `CARGO_TARGET_DIR` inside the container, so container and host build caches never mix.
- `quay.io/pypa/manylinux2014_x86_64` (glibc 2.17) is based on CentOS 7, whose package mirrors
  are archived. Prefer route A for `manylinux_2_17`.
- This route was **not run** on the release machine, which has no container runtime. Route A is the
  verified one; treat B as a starting point and apply the checklist.

## Verification checklist before publishing a wheel

1. **Tag:** `pip install auditwheel && auditwheel show dist-*/tenzorpipe-*.whl` must report the
   intended tag and no unexpected external libraries.
2. **Same output as the release binary:** in a fresh venv with the wheel installed:
   ```sh
   bash scripts/gen_skip_fixtures.sh /tmp/tenzor-skip-fixtures
   /path/to/venv/bin/python scripts/check_wheel_identity.py --binary bin/tenzor-linux-x86_64
   ```
3. **Runs on the oldest target distribution**, for example for `manylinux_2_28`:
   ```sh
   docker run --rm -v "$PWD":/io -w /io almalinux:8 bash -euxc '
     dnf install -y python39 python39-pip
     python3.9 -m pip install /io/dist-manylinux/tenzorpipe-*.whl
     python3.9 -c "import tenzorpipe as tp; print(tp.__version__, tp.ingest(\"fixtures/high-bframes.mp4\", \"/tmp/a.tenzor\"))"
   '
   ```
   (`centos:7` with its archived repositories plays the same role for `manylinux_2_17`.)
4. **License files:** the wheel's `dist-info/licenses/` must contain `LICENSE` and
   `THIRD_PARTY_NOTICES/` (maturin copies them from `pyproject.toml`'s `license-files`).

## Source installs

`pip install .` builds for the host glibc, so it works on any Linux x86-64 with Rust 1.98.1,
NASM and a C++ compiler, whatever the glibc version.
