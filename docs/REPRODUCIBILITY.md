# Reproduction guide

## Three different operations

1. **Summary reproduction** recomputes metrics from archived per-run results. It does not independently establish that those results were scored correctly.
2. **Offline trajectory replay** loads the exact source snapshot for each batch and re-executes recorded actions, checking messages, tool output, state and scores against the saved evidence. It does not rerun the LLM.
3. **New API replication** asks a live model to perform the frozen tasks in fresh sessions. It is a new experiment and may differ because of stochasticity, service changes or model-version drift.

## Environment

Use CPython 3.12. v5 records report Windows with Python 3.12.14. The software uses only the Python standard library. Python 3.11 provides `tomllib`, but cross-version floating-point summation can change last digits in serialized engineering feedback; use 3.12 for strict replay. The public wrapper normalizes Windows path separators when verifying v3's source snapshot.

```bash
python --version
python scripts/reproduce.py all
python scripts/test.py
```

Archive extraction plus replay scratch data can require more than 1 GB of local disk space. All writes go to `.repro/` by default. An existing per-batch replay workspace is rejected; choose a fresh `--output` to repeat the full check.

## Exact historical materials

For each batch, `data/batches/<id>/` exposes:

- `freeze.json`: frozen source and artifact hashes, model configuration and request budget.
- `prompts.json`: the exact prompt bundle. Chinese prompts are preserved, not translated.
- `tasks.json`: public requirements, scripted external replies, private evaluator targets and reference calculations.
- `schedule.json`: scheduled task/group/repetition order.
- `summary.json`, `results.csv` where present: historical aggregates and per-run values.
- `model_provenance.json`: requested configuration, returned model identifiers and fingerprint availability.
- `archive_members.sha256.json`: every exported archive member and its digest.

The word `private` in the task schema means hidden from the model during the experiment, not withheld from this release. Only current/past public requirements and the method's own interaction history enter the controller's messages. Future stages and scoring references are not sent to the model. Public release of answers does not turn these tasks into a contamination-resistant future benchmark; use them as a transparent reproduction suite.

The full ZIP archive additionally contains `source_snapshot/`, `runs/<id>/request_*.json`, `response_*.json`, actions, engineering events, input revisions, scripted review logs and saved states. `response_*.json` records the actual serialized request body as well as the saved response, where available. The original protocol contains JSON action definitions in prompts and parser/runtime code; this implementation uses JSON messages rather than provider-native function-call schemas.

## Keyless replay adaptations

Historical auditors read a key solely to check that it did not appear in logs. Public replay substitutes a distinctive non-secret marker and disables socket connections. It does not claim to repeat an original-key scan. The export-time scan against configured secrets is separately documented in `data/export_audit.json`.

The wrapper replaces the historical live-tree verification with archive/snapshot hash checks and normalizes path separators. It imports each batch's exact source in a separate process; neither engineering formulas, controller rules, model messages nor scoring criteria are altered. New audit files are written only to the extracted copy. Historical audit files remain in the archive.

## Starting a new model experiment

```bash
python scripts/rerun.py --batch v4_formal --output new_runs/replication_01 --dry-run
```

Set the API key in the local environment using your normal secret-management method. The runner expects `CHASSIS_API_KEY`, or an alternative variable named with `--api-key-env`. It does not print the key or save it in the experiment.

```bash
python scripts/rerun.py --batch v4_formal --output new_runs/replication_01 --allow-paid
```

The default provider URL and model are taken from the frozen configuration. To evaluate another compatible service, explicitly provide `--model` and/or `--base-url`. The original adapter sends the saved Chat Completions conventions, including JSON output and thinking settings. If a service rejects those fields, validate a separately versioned adapter on development tasks before freezing a new formal study; do not silently adjust the historical protocol or relabel new results as historical replication.

The runner uses `run_one` from the selected source snapshot, copies only the task/prompt/schedule into a new run, and stores public configuration provenance. Historical responses in `source_protocol/` are available for inspection but are never loaded into the new model context. The folder must be new; infrastructure errors stop the run for diagnosis. This first release intentionally offers no automatic overwrite or silent retry of an incomplete paid session.

## Model provenance and drift

Recorded responses identify the services as `DeepSeek-V4.1-Flash` and `GLM-5.1`. No non-null system fingerprints or underlying immutable weight revisions were returned. Exact internal reasoning behaviour is unknown. Request parameters are recorded, but they do not prove which provider-internal settings were applied.

A future replication must retain the new execution date, requested and returned model names, actual request body, finish reasons, usage and failures. Report it as a new collection period. Matching a service label cannot establish that the underlying model is unchanged.

## License and stable citation

The repository includes a MIT license and software citation metadata. Cite a release tag and commit hash rather than a moving branch. GitHub's official guidance explains [repository licensing](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository) and [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases). A tag alone is not claimed to be immutable.
