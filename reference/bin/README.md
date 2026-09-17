# Linux x86-64 comparison binaries

These binaries reproduce the old/new regression and benchmark checks. They are
not the current deployment build. v0.1.6 is the tensor-value oracle. v0.1.8 was
compiled from the unmodified user-supplied source and retains the reproduced
chunk-buffer accounting and emitter-panic defects. Run it only on the controlled
benchmark fixture. v0.1.9 is the byte-identity oracle for 0.2.0 audio changes. v0.2.0 is the byte-identity
oracle for the 0.3.0 non-reference skip and default-worker changes
(scripts/test_skip_identity.py). The current engine is in ../../bin/.
Hashes are listed in docs/releases/*/SHA256SUMS and the release evidence. Both comparison binaries
use the same permissive third-party dependencies and project license identifier.
