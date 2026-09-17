# Local OpenH264 Rust wrapper patch

Source: openh264 0.9.8, BSD-2-Clause. Configure nonzero decoder threads before
Initialize; skip the thread option for the unthreaded default. No codec math is
changed. Nonzero thread counts are available only in the explicitly experimental
TenzorPipe feature build and must pass fidelity, ordering and shutdown tests.
