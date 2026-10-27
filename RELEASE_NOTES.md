# v1.0.0 — Reproducibility release

Initial public release of code and frozen evidence supporting the v3-v5 experiments for *Large Language Model Agent-Assisted Chassis Design for Combine Harvesters*.

- 252 formal runs and 12 separately identified GLM interface-check runs.
- Exact prompt bundles, task definitions, scripted review replies, scoring references and source snapshots.
- Recorded API requests/responses, engineering actions, states and outcomes, including formal failures and the retained interrupted partial session.
- Keyless archive verification, summary reproduction and deterministic trajectory replay.
- An explicitly opt-in API runner that writes a fresh experiment without replacing historical records.
- Original source/evidence bytes and freeze hashes preserved. v1/v2 and private project documents excluded.

Validation: all seven batches passed integrity checks, 264 complete trajectories replayed, and 88 unit tests passed. No new model calls were made for this release.

The model-dependent result is retained: the agent missed fewer requirement updates in the tested DeepSeek setting, while all GLM groups passed and the fixed workflow required fewer model requests. This release does not claim universal agent superiority or vehicle-level validation.
