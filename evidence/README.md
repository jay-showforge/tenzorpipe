# Current evidence — v0.2.0 EPYC rerun

Current build/verification: build-v020.log, unit-v020.log, aac-unit-v020.log,
aac-unit-no-default-v020.log, fmt-v020.log, clippy-v020.log, licenses-v020.log,
verification-summary.json, matrix.json, failures.json, python-loader.json,
epoch-selection-regression.json, concurrency-tests.json, chunk-boundary-tests.json.

Current audio verification: v0.2.0/audio-identity-generated-media.json,
v0.2.0/identity-repo-fixtures.json, v0.2.0/corruption-fuzz.json,
audio-write-failure.json and corresponding top-level *v020.log files.
The supplied Claude logs/measurements are archived in docs/releases/v0.2.0-claude/.
Do not use historical v0.2.0/benchmark-v020.json as current EPYC performance evidence.

Current performance: epyc-benchmarks-v020.json, epyc-retained-v020.json,
epyc-benchmark-verification-v020.json, machine-v020.json,
worker-recommendation-v020.json, memory-release.json, disk-write-audit.json,
publication-stress.json. These records name the exact release binary and/or fixture
hashes in provenance-v020.json and fixture-inventory-v020.json.

Temporary absolute paths describe this run. Repeated benchmark outputs are deleted
only after independent validation; 25 representative/diagnostic artifacts were also
reread. Large artifacts and long/generated-extra media are not bundled. Recreate them
with scripts/reproduce.sh. Hashes, numerical comparisons and reconstructed images remain.

Older JSON/logs remain as history and are not current-release gates. v020-review/
records the invalid generated noise fixture and initial non-triggering write limit,
and explains their repairs. Matrix agreement alone does not prove valid media decoded.
