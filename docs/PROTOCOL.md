# Protocol and evidence index

| Paper concept | Original code label | Role |
|---|---|---|
| Direct design (v3) | M0 | LLM performs written engineering calculations and selects a candidate |
| Fixed workflow (v3) | M1 | LLM organizes inputs; a program schedules engineering tools |
| Agent workflow (v3) | M2 | LLM selects tools and the checking scope |
| Fixed sequential workflow | A (paper F) | LLM updates inputs; fixed scheduling executes engineering checks |
| General agent | B | LLM selects engineering actions from feedback |
| Skill-assisted agent | C (paper S) | Same agent with additional chassis-design guidance |

v3 uses two crawler configurations and three situations: complete requirements, clarification of missing/conflicting inputs, and requirement changes. Each situation has two tasks per configuration. The 12 tasks are each run twice for each method. A valid result must use current confirmed inputs, an applicable configuration and a feasible allowed candidate. Any candidate satisfying the applicable modeled constraints may pass.

v4 uses two configurations and three sequential-change situations: selection trade-offs (T1), specification retention (T2), and replacement required (T3). Each run contains initial design and two changes. All three stages must pass to count as a valid run. A repeated session or an additional stage is not an additional independent task. Public preferences and minimum margins also enter the quality checks.

v5 adds six T2 tasks for the DeepSeek service and repeats the original twelve v4 tasks with GLM. The GLM development check uses six separate development tasks and is excluded from formal comparisons. The S-group additions were collected on the next day and were not interleaved with F/B. One interrupted GLM S-group session is retained separately from its completed replacement.

## Material mapping

| Requirement | Location |
|---|---|
| Exact system/role prompts | Each batch's `prompts.json`; full messages in archive `runs/*/request_*.json` |
| Action JSON structures | Prompt bundle and frozen `prototype/*/runtime.py` parser/controller |
| Deterministic engineering functions | `prototype/engineering.py`, `dual_power.py`, `chassis_skill_v4/decision.py` |
| Frozen task definitions and scripted replies | Each batch's `tasks.json` (`public`, `external_policy`, stages) |
| Scoring reference and code | `tasks.json` evaluator sections and frozen `evaluate.py`, `reference.py`, `reference_metrics.py` |
| Model and request settings | `freeze.json`, `model_provenance.json`, actual request bodies in saved response records |
| Run order and repetition | `schedule.json` |
| Failure and interruption records | All formal runs are retained; v5 C GLM archive also has `interrupted_runs/` and an interruption note |
| Frozen software | `source_snapshot/` in each archive, checked against the original freeze hashes |
| Deterministic verification | `python scripts/reproduce.py all` |

The public interface uses action JSON in Chat Completions content. It is not a provider-native `tools` function-call API. Reporting the exact schema requires both the prompts and parsing/execution code, which are included.

## Engineering scope

The independent-HST case checks straight-line steady-state loading under its assumptions. The dual-power-stream case includes branch mapping with an externally supplied equivalent steering resistance moment. Neither establishes a complete transient hydraulic, soil-interaction, thermal, braking, structural or vehicle-level validation. Thus a blanket statement that both models omit all steering loads would be inaccurate.
