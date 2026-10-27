"""v5 supplementary experiments (2026-10-21).

Label A_spec_retained : 6 new T2 ("original specification retainable") tasks,
                        methods A (fixed) and B (general agent), DeepSeek-V4.1-Flash, 2 sessions.
Label B_glm_dev       : GLM-5.1 interface check on the 6 v4 development tasks, A and B, 1 session.
Label B_glm_formal    : GLM-5.1 replication of the 12 v4 formal tasks, A and B, 2 sessions.

The v4 Controller, prompts, tools, scoring and audit are reused unchanged; only task
selection, model configuration and output directory differ. Paper labels: A->F, B->B.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import random
from shutil import copy2
import statistics

from ..chat_api import load_config, public_settings
from ..process_comparison.util import read, write, sha, now
from ..chassis_skill_v4 import study as v4
from ..chassis_skill_v4.decision import digest, physical_case

PROJECT = v4.PROJECT
ROOT = PROJECT / "02_实验与验证" / "补充对照试验_v5_20261021"
V4ROOT = v4.ROOT
METHODS = ("A", "B")
LABELS = {
    "A_spec_retained": {"model": "deepseek_v4_1_flash", "tasks": "private/tasks_spec_retained.json",
                        "repeats": 2, "seed": 202610215011},
    "B_glm_dev": {"model": "glm_5_1", "tasks": "v4:development", "repeats": 1, "seed": 202610215021},
    "B_glm_formal": {"model": "glm_5_1", "tasks": "v4:formal", "repeats": 2, "seed": 202610215031},
}
say = v4.say


def settings(model):
    config = load_config(PROJECT / "models.toml", model)
    config["timeout_sec"], config["max_output_tokens"] = 600, 4096   # identical to v4 formal
    return config


def task_source(label):
    spec = LABELS[label]["tasks"]
    if spec.startswith("v4:"):
        return V4ROOT / "private" / f"tasks_{spec[3:]}.json"
    return ROOT / spec


def sources():
    own = sorted(Path(__file__).parent.glob("*.py"))
    return sorted(set(v4.sources()) | set(own) | {ROOT / "v5实验方案.md"})


def prepare():
    """Seeded construction of 6 new T2 tasks; predeclared criteria, no model outcomes."""
    from ..chassis_skill_v4.tasks import make_task
    target = ROOT / LABELS["A_spec_retained"]["tasks"]
    if target.exists():
        raise ValueError("task_materials_already_prepared")
    rng = random.Random(202610215001)
    rows = []
    for family in ("independent_hst", "dual_power_stream"):
        for variant in (1, 2, 1):
            rows.append(make_task(f"S{len(rows)+1:02d}", family, "T2", variant, rng))
    stripped = lambda case: digest(physical_case(case))
    previous, scanned = set(), []
    for p in [V4ROOT / "private/tasks_formal.json", V4ROOT / "private/tasks_development.json",
              PROJECT / "02_实验与验证/流程对照试验运行_20261020/formal/tasks.json",
              PROJECT / "02_实验与验证/流程对照试验运行_20261020/development_02/tasks.json"]:
        if p.exists():
            for t in read(p):
                stages = t.get("stages")
                if stages:
                    for s in stages:
                        tgt = s["private"]["target"]
                        previous.add(stripped(tgt["case"] if "case" in tgt else tgt))
                else:
                    tgt = t["private"]["target"]
                    previous.add(stripped(tgt["case"] if "case" in tgt else tgt))
            scanned.append(str(p.relative_to(PROJECT)).replace("\\", "/"))
    new = [stripped(s["private"]["target"]["case"]) for t in rows for s in t["stages"]]
    first = {stripped(t["stages"][0]["private"]["target"]["case"]) for t in rows}
    if len(first) != 6 or set(new) & previous:
        raise ValueError("duplicate_physical_task")
    write(target, rows)
    write(ROOT / "preparation.json", {
        "created_at": now(), "label": "A_spec_retained", "tasks": [t["id"] for t in rows],
        "category": "T2 (original specification retainable)", "families": {"independent_hst": 3, "dual_power_stream": 3},
        "generator": "prototype.chassis_skill_v4.tasks.make_task, unchanged", "seed": 202610215001,
        "selection": "predeclared v4 T2 criteria; seeded generation; no model outcomes used",
        "duplicate_scan_files": scanned, "prior_physical_states": len(previous), "exact_duplicates": 0,
        "tasks_sha256": sha(target)})
    say(status="prepared", tasks=len(rows))


def freeze(label):
    spec, folder = LABELS[label], ROOT / label
    if (folder / "freeze.json").exists():
        raise ValueError("freeze_already_exists")
    offline = ROOT / "offline_tests.json"
    if not offline.exists() or read(offline)["status"] != "passed":
        raise ValueError("offline_tests_required")
    if label == "B_glm_formal":
        dev = ROOT / "B_glm_dev"
        if read(dev / "summary.json")["completed_runs"] != 12 or read(dev / "audit.json")["status"] != "passed":
            raise ValueError("glm_development_check_required")
        if any(r["infrastructure_error"] for r in read(dev / "summary.json")["rows"]):
            raise ValueError("glm_development_infrastructure_error")
    tasks = read(task_source(label))
    write(folder / "tasks.json", tasks)
    write(folder / "prompts.json", v4.prompts())
    if read(folder / "prompts.json") != read(V4ROOT / "formal/prompts.json"):
        raise ValueError("prompts_differ_from_v4_formal")
    rng = random.Random(spec["seed"])
    blocks = [(t["id"], rep) for t in tasks for rep in range(1, spec["repeats"] + 1)]
    rng.shuffle(blocks)
    jobs = []
    for tid, rep in blocks:
        methods = list(METHODS)
        rng.shuffle(methods)
        jobs += [{"id": f"{tid}_{m}_r{rep}", "task_id": tid, "method": m, "repeat": rep} for m in methods]
    write(folder / "schedule.json", jobs)
    hashes = {}
    for path in sources():
        rel = str(path.relative_to(PROJECT)).replace("\\", "/")
        hashes[rel] = sha(path)
        dst = folder / "source_snapshot" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        copy2(path, dst)
    frozen = {"created_at": now(), "label": label, "runs": len(jobs), "files": hashes,
              "artifacts": {n: sha(folder / n) for n in ("tasks.json", "prompts.json", "schedule.json")},
              "task_source": str(task_source(label).relative_to(PROJECT)).replace("\\", "/"),
              "model_settings": public_settings(settings(spec["model"])),
              "budget": {"requests_per_stage": 24, "stages": 3, "questions_per_stage": 2,
                         "max_tokens_per_request": 4096, "network_retries_per_request": 1},
              "methods": list(METHODS)}
    frozen["freeze_id"] = digest(frozen)
    write(folder / "freeze.json", frozen)
    say(status="frozen", label=label, runs=len(jobs), freeze_id=frozen["freeze_id"][:12])


def run(label, allow_paid):
    if not allow_paid:
        raise ValueError("explicit_allow_paid_flag_required")
    folder = ROOT / label
    frozen = v4.verify(folder)
    config = settings(LABELS[label]["model"])
    if public_settings(config) != frozen["model_settings"]:
        raise ValueError("model_configuration_changed")
    tasks = {t["id"]: t for t in read(folder / "tasks.json")}
    bundle, schedule = read(folder / "prompts.json"), read(folder / "schedule.json")
    for i, job in enumerate(schedule):
        v4.verify(folder)
        result = v4.run_one(tasks[job["task_id"]], job, folder, bundle, config)
        summarize(label, quiet=True)
        say(label=label, completed=i + 1, planned=len(schedule), run_id=job["id"],
            valid=result["all_stages_valid"], calls=result["model_requests"])
        if result["infrastructure_error"]:
            raise ValueError("infrastructure_diagnosis_required")
    say(status="all_runs_complete", label=label, count=len(schedule))


def audit(label):
    from ..chassis_skill_v4 import audit as v4audit
    model = LABELS[label]["model"]
    original = v4audit.settings
    v4audit.settings = lambda: settings(model)       # credential scan uses this label's key
    try:
        return v4audit.audit(ROOT, label)
    finally:
        v4audit.settings = original


def rows_of(folder):
    return [read(p) for p in sorted(folder.glob("runs/*/result.json"))]


def summarize(label, quiet=False):
    folder = ROOT / label
    rows = rows_of(folder)
    grouped = {}
    for m in METHODS:
        g = [r for r in rows if r["method"] == m]
        if not g:
            continue
        grouped[m] = {"runs": len(g), "all_stages_valid": sum(r["all_stages_valid"] for r in g),
                      "all_stages_preferred": sum(r["all_stages_preferred"] for r in g),
                      "mean_requests": statistics.mean(r["model_requests"] for r in g),
                      "reported_tokens": sum(r["tokens"].get("total_tokens", 0) for r in g),
                      "mean_wall_time_s": statistics.mean(r["wall_time_s"] for r in g),
                      "by_category": {c: {"runs": sum(r["category"] == c for r in g),
                                          "valid": sum(r["category"] == c and r["all_stages_valid"] for r in g)}
                                      for c in ("T1", "T2", "T3")}}
    summary = {"label": label, "updated_at": now(), "completed_runs": len(rows), "methods": grouped, "rows": rows}
    write(folder / "summary.json", summary)
    with (folder / "results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "task_id", "repeat", "method", "family", "category",
                                          "all_stages_valid", "all_stages_preferred", "preference_rate",
                                          "model_requests", "wall_time_s", "infrastructure_error"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if not quiet:
        say(status="summarized", label=label, completed=len(rows),
            valid={m: v["all_stages_valid"] for m, v in grouped.items()})
    return summary


def paired(rows, field="all_stages_valid", a="B", b="A"):
    """Task-level paired analysis: mean over sessions, then B-A per task."""
    per = {}
    for r in rows:
        per.setdefault(r["task_id"], {}).setdefault(r["method"], []).append(float(r[field]))
    diffs = {t: statistics.mean(v[a]) - statistics.mean(v[b]) for t, v in per.items() if a in v and b in v}
    d = list(diffs.values())
    pos, neg = sum(x > 0 for x in d), sum(x < 0 for x in d)
    n = pos + neg
    p = min(1.0, 2 * sum(math.comb(n, k) for k in range(0, min(pos, neg) + 1)) / 2 ** n) if n else 1.0
    rng = random.Random(202610215099)
    boot = sorted(statistics.mean(rng.choices(d, k=len(d))) for _ in range(10000)) if d else []
    return {"tasks": len(d), "mean_difference_pp": round(100 * statistics.mean(d), 1) if d else None,
            "bootstrap95_pp": [round(100 * boot[249], 1), round(100 * boot[9749], 1)] if d else None,
            "tasks_favoring_B": pos, "tasks_favoring_A": neg, "ties": len(d) - n,
            "exact_sign_test_two_sided_p": round(p, 4), "per_task": diffs}


def report():
    out = {"created_at": now()}
    a = ROOT / "A_spec_retained"
    new_rows = rows_of(a)
    v4_rows = [r for r in rows_of(V4ROOT / "formal") if r["category"] == "T2" and r["method"] in METHODS]
    out["A_new_tasks_only"] = paired(new_rows)
    out["A_combined_T2_v4_plus_new"] = paired(v4_rows + new_rows)
    out["A_counts"] = {m: {"new": [sum(r["all_stages_valid"] for r in new_rows if r["method"] == m),
                                   sum(r["method"] == m for r in new_rows)],
                           "v4_T2": [sum(r["all_stages_valid"] for r in v4_rows if r["method"] == m),
                                     sum(r["method"] == m for r in v4_rows)]} for m in METHODS}
    g = rows_of(ROOT / "B_glm_formal")
    out["B_glm_all"] = paired(g)
    out["B_glm_T2"] = paired([r for r in g if r["category"] == "T2"])
    out["B_glm_counts"] = {m: {"valid": sum(r["all_stages_valid"] for r in g if r["method"] == m),
                               "runs": sum(r["method"] == m for r in g),
                               "mean_requests": round(statistics.mean(r["model_requests"] for r in g if r["method"] == m), 2)
                               if any(r["method"] == m for r in g) else None,
                               "by_category": {c: [sum(r["all_stages_valid"] for r in g if r["method"] == m and r["category"] == c),
                                                   sum(r["method"] == m and r["category"] == c for r in g)]
                                               for c in ("T1", "T2", "T3")}} for m in METHODS}
    d4 = [r for r in rows_of(V4ROOT / "formal") if r["method"] in METHODS]
    out["v4_deepseek_reference"] = {m: {"valid": sum(r["all_stages_valid"] for r in d4 if r["method"] == m),
                                        "runs": sum(r["method"] == m for r in d4)} for m in METHODS}
    write(ROOT / "v5_statistics.json", out)
    say(status="report_statistics_written")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("prepare", "freeze", "verify", "run", "summary", "audit", "report"))
    p.add_argument("--label", choices=tuple(LABELS))
    p.add_argument("--allow-paid", action="store_true")
    a = p.parse_args()
    try:
        if a.mode == "prepare": prepare()
        elif a.mode == "freeze": freeze(a.label)
        elif a.mode == "verify": say(status="verified", freeze_id=v4.verify(ROOT / a.label)["freeze_id"][:12])
        elif a.mode == "run": run(a.label, a.allow_paid)
        elif a.mode == "summary": summarize(a.label)
        elif a.mode == "audit": audit(a.label)
        else: report()
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError and str(exc).replace("_", "").isalnum() else "details_omitted"
        say(status="stopped", error_type=type(exc).__name__, code=code)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
