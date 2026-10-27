"""v5 addendum (2026-10-22): run the Skill agent (method C, paper label S) on the
v5 spec-retained tasks (DeepSeek) and on the v4 formal tasks (GLM-5.1).
Reuses frozen v4 controller/scoring/audit and v5 helpers without modifying them."""
import argparse, random, statistics
from pathlib import Path
from shutil import copy2

from ..chat_api import public_settings
from ..process_comparison.util import read, write, sha, now
from ..chassis_skill_v4 import study as v4
from ..chassis_skill_v4.decision import digest
from . import study as v5

ADD = {
    "C_spec_retained": {"model": "deepseek_v4_1_flash", "tasks": "private/tasks_spec_retained.json", "repeats": 2, "seed": 202610225011},
    "C_glm_formal": {"model": "glm_5_1", "tasks": "v4:formal", "repeats": 2, "seed": 202610225031},
}
v5.LABELS.update(ADD)          # lets v5.run / v5.audit / v5.settings resolve these labels
ROOT = v5.ROOT
say = v4.say


def sources():
    return sorted(set(v5.sources()) | {ROOT / "v5补测S组方案.md"})


def freeze(label):
    spec, folder = ADD[label], ROOT / label
    if (folder / "freeze.json").exists():
        raise ValueError("freeze_already_exists")
    off = ROOT / "offline_tests_S.json"
    if not off.exists() or read(off)["status"] != "passed":
        raise ValueError("offline_tests_required")
    tasks = read(v5.task_source(label))
    write(folder / "tasks.json", tasks)
    write(folder / "prompts.json", v4.prompts())
    if read(folder / "prompts.json") != read(v4.ROOT / "formal/prompts.json"):
        raise ValueError("prompts_differ_from_v4_formal")
    rng = random.Random(spec["seed"])
    blocks = [(t["id"], rep) for t in tasks for rep in range(1, spec["repeats"] + 1)]
    rng.shuffle(blocks)
    jobs = [{"id": f"{tid}_C_r{rep}", "task_id": tid, "method": "C", "repeat": rep} for tid, rep in blocks]
    write(folder / "schedule.json", jobs)
    hashes = {}
    for path in sources():
        rel = str(path.relative_to(v5.PROJECT)).replace("\\", "/")
        hashes[rel] = sha(path)
        dst = folder / "source_snapshot" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        copy2(path, dst)
    frozen = {"created_at": now(), "label": label, "runs": len(jobs), "files": hashes,
              "artifacts": {n: sha(folder / n) for n in ("tasks.json", "prompts.json", "schedule.json")},
              "task_source": str(v5.task_source(label).relative_to(v5.PROJECT)).replace("\\", "/"),
              "model_settings": public_settings(v5.settings(spec["model"])),
              "budget": {"requests_per_stage": 24, "stages": 3, "questions_per_stage": 2,
                         "max_tokens_per_request": 4096, "network_retries_per_request": 1},
              "methods": ["C"]}
    frozen["freeze_id"] = digest(frozen)
    write(folder / "freeze.json", frozen)
    say(status="frozen", label=label, runs=len(jobs), freeze_id=frozen["freeze_id"][:12])


def summarize(label):
    rows = v5.rows_of(ROOT / label)
    s = {"label": label, "updated_at": now(), "completed_runs": len(rows),
         "C": {"runs": len(rows), "all_stages_valid": sum(r["all_stages_valid"] for r in rows),
               "all_stages_preferred": sum(r["all_stages_preferred"] for r in rows),
               "mean_requests": statistics.mean(r["model_requests"] for r in rows) if rows else None,
               "reported_tokens": sum(r["tokens"].get("total_tokens", 0) for r in rows),
               "infrastructure_errors": sum(r["infrastructure_error"] for r in rows),
               "by_category": {c: [sum(r["all_stages_valid"] for r in rows if r["category"] == c),
                                   sum(r["category"] == c for r in rows)] for c in ("T1", "T2", "T3")}},
         "rows": rows}
    write(ROOT / label / "summary.json", s)
    say(status="summarized", label=label, completed=len(rows), valid=s["C"]["all_stages_valid"])
    return s


def report():
    sr = v5.rows_of(ROOT / "C_spec_retained"); ab = v5.rows_of(ROOT / "A_spec_retained")
    v4t2 = [r for r in v5.rows_of(v4.ROOT / "formal") if r["category"] == "T2"]
    gc = v5.rows_of(ROOT / "C_glm_formal"); gab = v5.rows_of(ROOT / "B_glm_formal")
    def cnt(rows, m): return [sum(r["all_stages_valid"] for r in rows if r["method"] == m), sum(r["method"] == m for r in rows)]
    def req(rows, m):
        x = [r["model_requests"] for r in rows if r["method"] == m]; return round(statistics.mean(x), 2) if x else None
    out = {"created_at": now(),
           "deepseek_new_T2": {m: {"valid": cnt(sr + ab, m), "mean_requests": req(sr + ab, m)} for m in ("A", "B", "C")},
           "deepseek_T2_pooled_10_tasks": {m: cnt(sr + ab + v4t2, m) for m in ("A", "B", "C")},
           "S_minus_B_pooled_T2": v5.paired(sr + ab + v4t2, a="C", b="B"),
           "S_minus_F_pooled_T2": v5.paired(sr + ab + v4t2, a="C", b="A"),
           "glm": {m: {"valid": cnt(gc + gab, m), "mean_requests": req(gc + gab, m),
                       "by_category": {c: cnt([r for r in gc + gab if r["category"] == c], m) for c in ("T1", "T2", "T3")}}
                   for m in ("A", "B", "C")},
           "glm_S_minus_B": v5.paired(gc + gab, a="C", b="B"),
           "note": "S runs were executed in a separate batch about one day after F/B; not interleaved."}
    write(ROOT / "v5_S_statistics.json", out)
    say(status="report_statistics_written")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("freeze", "run", "summary", "audit", "report"))
    p.add_argument("--label", choices=tuple(ADD))
    p.add_argument("--allow-paid", action="store_true")
    a = p.parse_args()
    try:
        if a.mode == "freeze": freeze(a.label)
        elif a.mode == "run": v5.run(a.label, a.allow_paid)
        elif a.mode == "summary": summarize(a.label)
        elif a.mode == "audit": v5.audit(a.label)
        else: report()
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError and str(exc).replace("_", "").isalnum() else "details_omitted"
        say(status="stopped", error_type=type(exc).__name__, code=code)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
