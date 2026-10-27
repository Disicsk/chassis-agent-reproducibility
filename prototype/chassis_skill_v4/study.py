"""V4 isolated development, freeze, API execution and outcome reporting."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import random
from shutil import copy2
import statistics
import time

from ..chat_api import load_config, public_settings, complete, ModelServiceError
from ..process_comparison.util import read, write, sha, now
from ..process_comparison.study import usage_from
from .decision import digest, physical_case
from .runtime import Controller, messages, parse
from .evaluate import score_stage, score_run

PROJECT = Path(__file__).resolve().parents[2]
PLAN = PROJECT / "02_实验与验证" / "Skill增强对照试验_v4_20261020"
ROOT = PROJECT / "02_实验与验证" / "Skill增强对照试验运行_v4_20261020"
METHODS = ("A", "B", "C")


def say(**kwargs):
    print(json.dumps(kwargs, ensure_ascii=True, allow_nan=False), flush=True)


def sources():
    return sorted(set([*Path(__file__).parent.glob("*.py"),
                       *Path(__file__).parent.parent.glob("*.py"),
                       *Path(__file__).parent.parent.joinpath("process_comparison").glob("*.py"),
                       Path(__file__).parent.parent / "process_comparison/engineering_spec.txt",
                       *PLAN.joinpath("prompts").glob("*.txt"),
                       *PLAN.joinpath("chassis-design-skill").rglob("*.md")]))


def prompts():
    skill = (PLAN / "chassis-design-skill/SKILL.md").read_text(encoding="utf-8")
    skill_body = skill.split("---", 2)[2].strip()
    return {"common": (PLAN / "prompts/common.txt").read_text(encoding="utf-8"),
            "A": (PLAN / "prompts/A_fixed.txt").read_text(encoding="utf-8"),
            "B": (PLAN / "prompts/B_agent.txt").read_text(encoding="utf-8"),
            "skill_body": skill_body,
            "contract": (PLAN / "chassis-design-skill/references/decision-contract.md").read_text(encoding="utf-8"),
            "engineering_spec": (PROJECT / "prototype/process_comparison/engineering_spec.txt").read_text(encoding="utf-8")}


def prepare(root):
    from .tasks import build_tasks
    if (root / "private/tasks_formal.json").exists():
        raise ValueError("task_materials_already_prepared")
    all_tasks = {}
    for suite in ("development", "formal"):
        all_tasks[suite] = build_tasks(suite)
        write(root / "private" / f"tasks_{suite}.json", all_tasks[suite])
    # Duplicate checks exclude title/identifier; repeat physical inputs in T1 are intentional.
    def stripped(case):
        return digest(physical_case(case))
    previous = set()
    old_sets = [
        PROJECT / "02_实验与验证/流程对照试验运行_20261020/formal/tasks.json",
        PROJECT / "02_实验与验证/流程对照试验运行_20261020/development_02/tasks.json"]
    scanned = []
    for p in old_sets:
        if p.exists():
            rows = read(p)
            previous.update(stripped(x["private"]["target"]) for x in rows)
            scanned.append(str(p.relative_to(PROJECT)))
    for name in ("补充实验运行包_20261019", "补充实验运行包_v2_20261020"):
        base = PROJECT / "02_实验与验证" / name / "formal/private"
        # Legacy schema inspection stays local; traverse only case-like objects.
        def collect(value):
            if isinstance(value, dict):
                if all(k in value for k in ("requirements", "scenarios", "illustrative_catalog")):
                    previous.add(stripped(value))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        for p in base.glob("*.json"):
            collect(read(p))
            scanned.append(str(p.relative_to(PROJECT)))
    dev = {stripped(t["stages"][0]["private"]["target"]["case"]) for t in all_tasks["development"]}
    formal = {stripped(t["stages"][0]["private"]["target"]["case"]) for t in all_tasks["formal"]}
    if len(dev) != 6 or len(formal) != 12 or dev & formal or formal & previous:
        raise ValueError("duplicate_physical_task")
    write(root / "preparation.json", {"created_at": now(), "development_tasks": 6, "formal_tasks": 12,
          "stages_per_task": 3, "selection": "predeclared physical criteria and seeded generation, no model outcomes",
          "duplicate_scan_files": scanned, "prior_physical_states": len(previous), "exact_duplicates": 0,
          "crosscheck": "independent equations and preference implementation checked for all stages",
          "task_visibility": "current and past public inputs only; future stages and reference never sent"})
    say(status="prepared", development_tasks=6, formal_tasks=12, formal_runs=72)


def settings():
    config = load_config(PROJECT / "models.toml")
    # Same transport allowance for every group; output/request budgets unchanged.
    config["timeout_sec"], config["max_output_tokens"] = 600, 4096
    return config


def freeze(root, suite, label):
    folder = root / label
    if (folder / "freeze.json").exists():
        raise ValueError("freeze_already_exists")
    if not (root / "offline_tests.json").exists() or read(root / "offline_tests.json")["status"] != "passed":
        raise ValueError("offline_tests_required")
    if suite == "formal":
        completed = [p for p in root.glob("development_*/summary.json") if read(p)["completed_runs"] == 18]
        if not completed:
            raise ValueError("complete_development_required")
        latest = sorted(completed)[-1].parent
        verify(latest)
        audit = read(latest / "audit.json") if (latest / "audit.json").exists() else {}
        if audit.get("status") != "passed":
            raise ValueError("development_audit_required")
    tasks = read(root / "private" / f"tasks_{suite}.json")
    write(folder / "tasks.json", tasks)
    write(folder / "prompts.json", prompts())
    rng = random.Random(202610204011 if suite == "formal" else 202610204010)
    blocks = [(t["id"], rep) for t in tasks for rep in (range(1, 3) if suite == "formal" else (1,))]
    rng.shuffle(blocks)
    jobs = []
    for tid, rep in blocks:
        methods = list(METHODS)
        rng.shuffle(methods)
        jobs += [{"id": f"{tid}_{m}_r{rep}", "task_id": tid, "method": m, "repeat": rep}
                 for m in methods]
    write(folder / "schedule.json", jobs)
    hashes = {}
    for path in sources():
        rel = str(path.relative_to(PROJECT)).replace("\\", "/")
        hashes[rel] = sha(path)
        target = folder / "source_snapshot" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(path, target)
    copy2(PLAN / "v4实验方案.md", folder / "plan_snapshot.md")
    frozen = {"created_at": now(), "suite": suite, "label": label, "runs": len(jobs),
              "files": hashes, "artifacts": {n: sha(folder / n) for n in
                   ("tasks.json", "prompts.json", "schedule.json", "plan_snapshot.md")},
              "model_settings": public_settings(settings()),
              "budget": {"requests_per_stage": 24, "stages": 3, "questions_per_stage": 2,
                         "max_tokens_per_request": 4096, "network_retries_per_request": 1},
              "scoring_unit": "12 tasks; 2 repeated sessions; stages nested within task"}
    frozen["freeze_id"] = digest(frozen)
    write(folder / "freeze.json", frozen)
    say(status="frozen", label=label, runs=len(jobs), freeze_id=frozen["freeze_id"][:12])


def verify(folder):
    frozen = read(folder / "freeze.json")
    if digest({k: v for k, v in frozen.items() if k != "freeze_id"}) != frozen["freeze_id"]:
        raise ValueError("freeze_manifest_changed")
    for rel, expected in frozen["files"].items():
        if sha(PROJECT / rel) != expected or sha(folder / "source_snapshot" / rel) != expected:
            raise ValueError("frozen_source_mismatch")
    for name, expected in frozen["artifacts"].items():
        if sha(folder / name) != expected:
            raise ValueError("frozen_artifact_mismatch")
    return frozen


def run_one(task, job, folder, bundle, config):
    directory = folder / "runs" / job["id"]
    if (directory / "result.json").exists():
        return read(directory / "result.json")
    if directory.exists() and any(directory.glob("request_*.json")):
        raise ValueError("partial_paid_run_needs_record_recovery")
    wall_start = time.perf_counter()
    write(directory / "metadata.json", {**job, "started_at": now(), "fresh_context": True})
    flow = Controller(task["public"], job["method"], directory)
    records, stage_results, infrastructure_error = [], [], False
    for index, stage in enumerate(task["stages"]):
        flow.begin_stage(index, stage["public"], stage["external_policy"])
        start_count, before = len(records), flow.engine.counts.copy()
        stage_start = time.perf_counter()
        while len(records) - start_count < 24 and not flow.terminal:
            context = messages(flow, len(records) - start_count, bundle)
            attempt = 0
            while True:
                number = len(records) + 1
                write(directory / f"request_{number:03d}.json",
                      {"time": now(), "request_index": number, "stage": index,
                       "stage_request_index": number - start_count, "messages": context,
                       "input_version": flow.version, "transport_attempt": attempt})
                try:
                    record = complete(config, context, max_tokens=4096)
                except ModelServiceError as exc:
                    record = {"error": str(exc), "response": None}
                except Exception:
                    record = {"error": "UNEXPECTED_CLIENT_EXCEPTION; details omitted", "response": None}
                record.update(request_index=number, stage=index)
                records.append(record)
                write(directory / f"response_{number:03d}.json", record)
                if "error" in record:
                    if record["error"].startswith("NETWORK_OR_TIMEOUT") and attempt == 0 and len(records)-start_count < 24:
                        attempt += 1
                        continue
                    flow.terminal = "service_error"
                    infrastructure_error = True
                break
            if infrastructure_error:
                break
            action, error, edits = (parse(record["response"]["content"])
                                    if record["response"]["finish_reason"] == "stop"
                                    else (None, "incomplete_action_output", []))
            if error:
                flow.format_error(error)
            else:
                if edits:
                    flow.append("normalization_log.jsonl", {"request_index": number,
                                "edits": edits, "normalized": action})
                try:
                    flow.execute(action)
                except Exception as exc:
                    flow.terminal, infrastructure_error = "controller_error", True
                    flow.append("errors.jsonl", {"stage": index, "type": type(exc).__name__,
                                                "code": "CONTROLLER_EXCEPTION"})
            flow.save()
        if not flow.terminal:
            flow.terminal = "request_budget_exhausted"
        flow.save()
        snapshot = flow.snapshot()
        write(directory / f"state_stage_{index}.json", snapshot)
        stage_usage = {**usage_from(records[start_count:]),
                       "wall_time_s": round(time.perf_counter()-stage_start, 3),
                       "work": {k: flow.engine.counts[k]-before[k] for k in
                                ("computed_candidate_scenarios", "reused_candidate_scenarios")}}
        stage_results.append(score_stage(task, index, snapshot, stage_usage))
        flow.deliveries.append({"stage": index, "terminal": flow.terminal, "final": flow.final})
        if infrastructure_error:
            break
    usage = {**usage_from(records), "wall_time_s": round(time.perf_counter()-wall_start, 3),
             "work": dict(flow.engine.counts), "counts": dict(flow.counts),
             "infrastructure_error": infrastructure_error, "finished_at": now()}
    result = score_run(task, job, stage_results, usage)
    write(directory / "result.json", result)
    return result


def run(root, label, allow_paid):
    if not allow_paid:
        raise ValueError("explicit_allow_paid_flag_required")
    folder = root / label
    frozen = verify(folder)
    config = settings()
    if public_settings(config) != frozen["model_settings"]:
        raise ValueError("model_configuration_changed")
    tasks = {t["id"]: t for t in read(folder / "tasks.json")}
    bundle, schedule = read(folder / "prompts.json"), read(folder / "schedule.json")
    for i, job in enumerate(schedule):
        verify(folder)
        result = run_one(tasks[job["task_id"]], job, folder, bundle, config)
        summarize(root, label, quiet=True)
        say(label=label, completed=i+1, planned=len(schedule), run_id=job["id"],
            valid=result["all_stages_valid"], preferred=result["all_stages_preferred"],
            calls=result["model_requests"])
        if result["infrastructure_error"]:
            raise ValueError("infrastructure_diagnosis_required")
    say(status="all_runs_complete", label=label, count=len(schedule))


def contrast(rows, a, b, field):
    task_means = {}
    for row in rows:
        value = (row["work"].get("computed_candidate_scenarios", 0) if field == "computed"
                 else row[field])
        task_means.setdefault(row["task_id"], {}).setdefault(row["method"], []).append(value)
    differences = [statistics.mean(v[a])-statistics.mean(v[b]) for v in task_means.values()
                   if a in v and b in v]
    if not differences:
        return None
    rng = random.Random(202610204017)
    sample = sorted(statistics.mean(rng.choices(differences, k=len(differences))) for _ in range(10000))
    return {"tasks": len(differences), "difference": statistics.mean(differences),
            "exploratory_task_bootstrap_95": [sample[249], sample[9749]]}


def summarize(root, label, quiet=False, intervals=False):
    folder = root / label
    rows = [read(p) for p in sorted(folder.glob("runs/*/result.json"))]
    grouped = {}
    blocks = {}
    for row in rows:
        blocks.setdefault((row["task_id"], row["repeat"]), {})[row["method"]] = row["all_stages_valid"]
    common = {key for key, value in blocks.items() if len(value) == 3 and all(value.values())}
    for method in METHODS:
        group = [r for r in rows if r["method"] == method]
        if not group:
            continue
        paired = [r for r in group if (r["task_id"], r["repeat"]) in common]
        grouped[method] = {
            "runs": len(group), "all_stages_valid": sum(r["all_stages_valid"] for r in group),
            "all_stages_preferred": sum(r["all_stages_preferred"] for r in group),
            "mean_stage_preference_rate": statistics.mean(r["preference_rate"] for r in group),
            "requests": sum(r["model_requests"] for r in group),
            "mean_requests": statistics.mean(r["model_requests"] for r in group),
            "reported_tokens": sum(r["tokens"].get("total_tokens", 0) for r in group),
            "usage_known_responses": sum(r["usage_known_responses"] for r in group),
            "mean_wall_time_s": statistics.mean(r["wall_time_s"] for r in group),
            "mean_computed": statistics.mean(r["work"].get("computed_candidate_scenarios", 0) for r in group),
            "mean_reused": statistics.mean(r["work"].get("reused_candidate_scenarios", 0) for r in group),
            "question_rounds": sum(r["counts"].get("question_rounds", 0) for r in group),
            "format_errors": sum(r["counts"].get("format_errors", 0) for r in group),
            "by_category": {c: {"runs": sum(r["category"] == c for r in group),
                               "valid": sum(r["category"] == c and r["all_stages_valid"] for r in group),
                               "preferred": sum(r["category"] == c and r["all_stages_preferred"] for r in group)}
                            for c in ("T1", "T2", "T3")},
            "common_valid_runs": len(paired),
            "common_valid_mean_requests": statistics.mean(r["model_requests"] for r in paired) if paired else None,
            "common_valid_mean_computed": statistics.mean(r["work"].get("computed_candidate_scenarios", 0) for r in paired) if paired else None,
        }
    contrasts = ({f"{a}-{b}": {f: contrast(rows, a, b, f) for f in
                  ("all_stages_valid", "preference_rate", "model_requests", "computed")}
                  for a,b in (("C","B"),("C","A"))} if intervals else {})
    summary = {"label": label, "updated_at": now(), "completed_runs": len(rows),
               "methods": grouped, "contrasts": contrasts, "rows": rows}
    write(folder / "summary.json", summary)
    with (folder / "results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id","task_id","repeat","method","family","category",
            "all_stages_valid","all_stages_preferred","preference_rate","model_requests","wall_time_s"],
            extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    if not quiet:
        say(status="summarized", label=label, completed=len(rows),
            valid={m:v["all_stages_valid"] for m,v in grouped.items()},
            preferred={m:v["all_stages_preferred"] for m,v in grouped.items()})
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare","freeze","verify","run","summary","audit"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--suite", choices=("development","formal"), default="development")
    parser.add_argument("--label", default="development_01")
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode == "prepare": prepare(args.root)
        elif args.mode == "freeze": freeze(args.root, args.suite, args.label)
        elif args.mode == "verify":
            say(status="verified", freeze_id=verify(args.root / args.label)["freeze_id"][:12])
        elif args.mode == "run": run(args.root, args.label, args.allow_paid)
        elif args.mode == "summary": summarize(args.root, args.label, intervals=True)
        else:
            from .audit import audit
            audit(args.root, args.label)
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError and str(exc).replace("_","").isalnum() else "details_omitted"
        say(status="stopped", error_type=type(exc).__name__, code=code)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
