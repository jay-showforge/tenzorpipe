# Verification issues found during the EPYC rerun

The supplied license exception still named root v0.1.9; changed it to =0.2.0 only.
The new identity and corruption scripts printed failures but did not fail exit status;
added assertions, successful-input requirements, partial-output checks and streamed hashes.
The historical 432-case repository matrix repeated three fixtures twice; the current
matrix deduplicates them (396 cases: 336 artifacts +60 expected errors).

An initial generated noise-320k.mp4 lacked a moov box (262188 bytes); FFprobe and both
engines rejected it. Initial agreement was 156 successful artifacts +12 error agreements,
not 168 successful decodes. Regenerated that fixture through /tmp with a fixed noise
seed, confirmed it using FFprobe and reran all168 checks with REQUIRE_SUCCESS=1.
Other newly generated inputs also pass FFprobe. No engine parser check was relaxed.

A first write-limit test used 100000 bytes; the short WAV output was smaller and correctly
succeeded. Reduced the cap to10000 bytes so every chosen output actually exceeds it.
All12 old/new cases now encounter EFBIG, preserve the original diagnostic and remove
partial output. This is RLIMIT_FSIZE, not a claim of physically filling a filesystem.

The supplied engine source and floating-point algorithms were accepted unchanged.
