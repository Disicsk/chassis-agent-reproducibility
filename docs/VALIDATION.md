# Release validation (2026-10-27)

The public export was checked before publication.

| Check | Outcome |
|---|---|
| Original freeze IDs, source snapshots and frozen artifact hashes | Passed for all 7 released batches |
| Archive member hashes and browsable metadata copies | Passed |
| Per-run summary and principal paired-statistic reproduction | Passed; reported counts and intervals reproduced |
| v3 recorded-message, tool, state and scoring replay | 72/72 passed |
| v4 recorded-message, tool, state and scoring replay | 72/72 passed |
| v5 new DeepSeek T2, F/B | 24/24 replayed |
| v5 GLM interface check | 12/12 replayed; excluded from formal results |
| v5 GLM formal F/B | 48/48 replayed |
| v5 DeepSeek S addition | 12/12 replayed |
| v5 GLM S addition | 24/24 replayed |
| Released unit-test suite | 88 tests passed |
| New API runner dry run | Passed; no credentials read and no requests sent |

Replay success means that the saved execution and scoring were reconstructed. It does not mean all 264 designs passed: unsuccessful formal designs remain in the data and reproduce as unsuccessful. The retained interrupted partial session is outside these 264 completed runs and is not counted as another formal result.

No paid API calls were made while preparing this release. The public reproduction scripts disable socket connections during replay/tests. The tested environment was Windows with CPython 3.12; a separate Linux execution has not been claimed.

Two packaging issues were caught and resolved before publication: a missing development-task fixture needed by unit tests, and a sandbox temporary-directory permission problem. The fixture is an unchanged copy of the released GLM interface-check tasks; the test entry point uses an ignored local temporary directory. No historical experimental inputs, prompts, results or source snapshots were modified.

Export checks against locally configured secrets and credential patterns passed. These checks do not establish provider-side model identity, lack of training contamination, correctness of every natural-language explanation or real-engineer approval.
