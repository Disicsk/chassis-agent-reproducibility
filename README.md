# Chassis Agent

Reproducibility materials for **Large Language Model Agent-Assisted Chassis Design for Combine Harvesters**.

[中文说明](README_zh.md) · [Reproduction guide](docs/REPRODUCIBILITY.md) · [Protocol and evidence index](docs/PROTOCOL.md) · [Interpretation and limitations](docs/RESULTS.md)

This repository provides deterministic engineering tools, LLM workflow controllers, frozen synthetic tasks, exact prompts, scripted reviews, scoring code, and recorded experiments. It covers two crawler chassis configurations: independent dual HST and a dual-power-stream planetary transmission. It is intended for preliminary component sizing and workflow research.

The main contribution is traceable execution linking current requirements, engineering checks and design outputs. Autonomous scheduling did not improve every tested model and did not reduce model requests relative to the fixed workflow.

## Quick start (offline, no API key)

Use **Python 3.12** (the v5 collection environment reported 3.12.14). No third-party Python packages are required.

```bash
git clone https://github.com/Disicsk/chassis-agent-reproducibility.git
cd chassis-agent-reproducibility
python scripts/reproduce.py verify
python scripts/reproduce.py summarize
python scripts/reproduce.py replay --batch v4_formal
python scripts/test.py
```

To check every released batch, including the separate GLM interface check:

```bash
python scripts/reproduce.py all
```

Outputs go to a new, ignored `.repro/` directory. These commands do not contact a model service. Replay uses recorded model responses to reconstruct tool execution; it is not a new LLM experiment.

## Included experiments

| Experiment | Tasks and sessions | Results |
|---|---|---|
| v3 design workflow comparison | 12 tasks × 3 methods × 2 sessions = 72 runs | Direct 21/24; fixed 23/24; agent 24/24 |
| v4 sequential changes, DeepSeek | 12 tasks × 3 methods × 2 sessions = 72 runs | Fixed 18/24; agent 23/24; skill agent 23/24 |
| v5 new specification-retention tasks, DeepSeek | 6 tasks × 3 methods × 2 sessions = 36 runs | Fixed 6/12; agent 12/12; skill agent 11/12 |
| v5 replication, GLM | 12 tasks × 3 methods × 2 sessions = 72 runs | All groups 24/24; fixed uses 36.1% fewer requests than agent |
| GLM interface check | 6 development tasks × 2 methods = 12 runs | Excluded from formal result counts |

There are **252 formal runs** and **12 interface-check runs**. v5 skill-agent groups were collected separately on the following day. One interrupted partial session is retained and excluded from the scored count; its fresh replacement is identified in the archive. Formal failures are included. v1 and v2 are not included in this release.

The reported service identifiers are `DeepSeek-V4.1-Flash` and `GLM-5.1`. Saved responses do not expose an underlying weight revision or a non-null system fingerprint. These identifiers establish what the service reported, not a verified internal model snapshot.

## What is in the repository?

| Path | Purpose |
|---|---|
| `prototype/` | Current engineering code, workflow controllers, adapters and tests |
| `02_实验与验证/` | Original prompt templates, engineering decision guidance and protocol plans; Chinese paths are retained for historical code compatibility |
| `data/batches/<batch>/` | Browsable frozen tasks, prompts, schedule, results, original audit and model provenance |
| `data/archives/<batch>.zip` | Complete selected batch records, including exact source snapshots and request/response/action/state files |
| `data/manifest.json` | Batch scope, counts, archive hashes and collection identifiers |
| `data/original_reports/` | Original paired-statistics files and v5 plans |
| `scripts/` | Portable verification, summary, offline replay and fresh API rerun entry points |

The archives are compressed to keep the repository manageable. Their source and evidence file contents are unchanged. No credentials, private project documents, manuscript files or reviewer correspondence are distributed.

## Optional new API experiment

Inspect the proposed run without reading credentials or making requests:

```bash
python scripts/rerun.py --batch v4_formal --output new_runs/my_replication --dry-run
```

Only after setting `CHASSIS_API_KEY` in your local environment, opt in explicitly:

```bash
python scripts/rerun.py --batch v4_formal --output new_runs/my_replication --allow-paid
```

This command creates a new experiment and can incur API charges. It never replaces historical results. `--model` and `--base-url` support a compatible Chat Completions service; interface changes require separate development validation. See the reproduction guide for boundaries and recording requirements.

## Interpretation

Cross-model differences are part of the result. In the tested DeepSeek setting, the agent missed fewer requirement updates. In the GLM setting, fixed and agent workflows both passed the tested tasks, while the fixed workflow needed fewer requests. The evidence supports workflow selection conditioned on model behaviour and task characteristics; it does not establish universal agent superiority, human-time savings, or vehicle-level performance improvements.

The combined specification-retention comparison includes the four original discovery tasks and six added tasks. Its task-level paired sign test gives p = 0.0312 (rounded to 0.031 in the paper), but the six new tasks alone give p = 0.25. The combined finding is exploratory, not an independent confirmatory replication.

## License and citation

The authors' code and accompanying original research materials in this repository are released under the [MIT license](LICENSE). External model services remain governed by their providers' terms. See [CITATION.cff](CITATION.cff) for software citation metadata. The repository does not imply that the associated manuscript has been accepted or published.
