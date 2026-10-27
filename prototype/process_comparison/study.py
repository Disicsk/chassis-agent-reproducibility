"""Reproducible preparation, freezing, serial API runs and paired summaries."""
import argparse
import csv
import json
import random
import statistics
import time
from shutil import copy2
from collections import Counter
from pathlib import Path
from ..chat_api import load_config, public_settings, complete, ModelServiceError
from .runtime import Flow, messages, parse
from .evaluate import score
from .util import now, read, write, sha, digest

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT / "02_实验与验证" / "流程对照试验运行_20261020"
PLAN = PROJECT / "02_实验与验证" / "流程对照试验_20261020"
METHODS = ("M0", "M1", "M2")


def say(**values):
    print(json.dumps(values, ensure_ascii=True), flush=True)


def source_files():
    return sorted([*Path(__file__).parent.glob("*.py"), Path(__file__).with_name("engineering_spec.txt"),
                   *Path(__file__).parent.parent.glob("*.py")])


def prompt_bundle():
    files = {n: (PLAN / "prompts" / (n + ".txt")) for n in ["common", *METHODS, "task_message"]}
    return {**{n: p.read_text(encoding="utf-8") for n, p in files.items()},
            "engineering_spec": Path(__file__).with_name("engineering_spec.txt").read_text(encoding="utf-8")}


def prepare(root):
    from .tasks import build_tasks
    if (root / "private" / "tasks_formal.json").exists():
        raise ValueError("prepared_task_files_already_exist")
    for suite in ("development", "formal"):
        tasks = build_tasks(suite)
        write(root / "private" / f"tasks_{suite}.json", tasks)
    write(root / "preparation.json", {
        "created_at": now(), "development_tasks": 6, "formal_tasks": 12,
        "selection": "seeded physical construction; no model results used for selection",
        "crosscheck": "independent equations checked against all deterministic tool candidate/scenario constraints",
        "source_examples": "two original illustrative engineering inputs; not old benchmark targets or model answers",
        "no_task_content_in_console": True})
    say(status="prepared", development_tasks=6, formal_tasks=12)


def freeze(root, suite, label):
    folder = root / label
    if (folder / "freeze.json").exists():
        raise ValueError("freeze_already_exists")
    tasks = read(root / "private" / f"tasks_{suite}.json")
    if suite == "formal":
        devs = [p for p in root.glob("development_*/summary.json") if read(p)["completed_runs"] == 18]
        if not devs:
            raise ValueError("all_18_development_runs_required_before_formal_freeze")
        dev = sorted(devs)[-1]
        verify(dev.parent)
        if not (root / "offline_tests.json").exists() or read(root / "offline_tests.json")["status"] != "passed":
            raise ValueError("offline_tests_required")
    config = load_config(PROJECT / "models.toml")
    config["timeout_sec"] = 180
    config["max_output_tokens"] = 4096
    prompts = prompt_bundle()
    write(folder / "prompts.json", prompts)
    write(folder / "tasks.json", tasks)
    jobs, rng = [], random.Random(2026102088 if suite == "formal" else 2026102048)
    blocks = [(t["id"], rep) for t in tasks for rep in range(1, (3 if suite == "formal" else 2))]
    rng.shuffle(blocks)
    for tid, rep in blocks:
        methods = list(METHODS)
        rng.shuffle(methods)
        jobs.extend({"id": f"{tid}_R{rep}_{m}", "task_id": tid, "repeat": rep, "method": m} for m in methods)
    write(folder / "schedule.json", jobs)
    manifest = {
        "created_at": now(), "suite": suite, "label": label, "runs": len(jobs),
        "files": {str(p.relative_to(PROJECT)): sha(p) for p in source_files()},
        "artifacts": {n: sha(folder / n) for n in ("tasks.json", "prompts.json", "schedule.json")},
        "model_settings": public_settings(config),
        "request_settings": {"temperature": .2, "thinking": "disabled", "max_tokens": 4096,
                             "timeout_sec": 180, "max_requests": 36, "max_questions": 3,
                             "network_retries_per_request": 1},
        "unit": "12 tasks formal; two independent design sessions per method/task",
        "policy": "no changes based on formal outcomes; never feed private reference to a method"}
    manifest["freeze_id"] = digest(manifest)
    write(folder / "freeze.json", manifest)
    for source in source_files():
        destination = folder / "source_snapshot" / source.relative_to(PROJECT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, destination)
    say(status="frozen", suite=suite, jobs=len(jobs), label=label, freeze_id=manifest["freeze_id"][:12])


def verify(folder):
    freeze = read(folder / "freeze.json")
    for rel, expected in freeze["files"].items():
        if sha(PROJECT / rel) != expected:
            raise ValueError("frozen_source_hash_mismatch")
    for name, expected in freeze["artifacts"].items():
        if sha(folder / name) != expected:
            raise ValueError("frozen_artifact_hash_mismatch")
    return freeze


def usage_from(records):
    counts = Counter()
    known = 0
    models, finishes = Counter(), Counter()
    for r in records:
        response = r.get("response") or {}
        if response:
            models[str(response.get("model"))] += 1
            finishes[str(response.get("finish_reason"))] += 1
        use = response.get("usage")
        if isinstance(use, dict):
            known += 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(use.get(k), int):
                    counts[k] += use[k]
    return {"model_requests": len(records), "usage_known_responses": known,
            "tokens": dict(counts), "response_models": dict(models), "finish_reasons": dict(finishes),
            "service_error_requests": sum("error" in r for r in records)}


def run_one(task, job, folder, prompts, config):
    directory = folder / "runs" / job["id"]
    if (directory / "result.json").exists():
        return read(directory / "result.json")
    if (directory / "request_001.json").exists():
        # Never silently replay a partially paid session. Existing attempts remain visible.
        raise ValueError("incomplete_run_requires_record_recovery_not_replay")
    started = time.perf_counter()
    write(directory / "metadata.json", {**job, "started_at": now(), "fresh_context": True})
    flow = Flow(task["public"], task["external_policy"], job["method"], directory)
    # M1 first receives the user's current request. Its fixed pipeline starts only
    # after the LLM applies current input, never by precomputing a supplied old case.
    records, schema_errors = [], 0
    while len(records) < 36 and not flow.terminal:
        request_messages = messages(flow, task["id"], len(records), prompts)
        transport_attempt = 0
        while True:
            number = len(records) + 1
            write(directory / f"request_{number:03d}.json",
                  {"time": now(), "request_index": number, "messages": request_messages,
                   "transport_attempt": transport_attempt, "input_version": flow.version})
            try:
                record = complete(config, request_messages, max_tokens=4096)
            except ModelServiceError as exc:
                record = {"error": str(exc), "response": None}
            except Exception:
                record = {"error": "UNEXPECTED_CLIENT_EXCEPTION; details omitted", "response": None}
            record["request_index"] = number
            records.append(record)
            write(directory / f"response_{number:03d}.json", record)
            if "error" in record:
                if record["error"].startswith("NETWORK_OR_TIMEOUT") and transport_attempt == 0 and len(records) < 36:
                    transport_attempt += 1
                    continue
                flow.terminal = "service_error"
            break
        if flow.terminal:
            break
        if record["response"]["finish_reason"] != "stop":
            action, error, edits = None, "incomplete_action_output", []
        else:
            action, error, edits = parse(record["response"]["content"])
        if error:
            schema_errors += 1
            event = {"time": now(), "input_version": flow.version, "action": {"name": "format_error"},
                     "feedback": {"status": error, "instruction": "按系统提示的动作JSON结构重写；不要工程工具或额外包装。"}}
            flow.history.append(event)
            flow.append("actions.jsonl", event)
        else:
            if edits:
                flow.append("normalization_log.jsonl", {"request_index": number, "edits": edits, "normalized": action})
            try:
                flow.execute(action)
            except Exception:
                # Prevent accidental printing of a task or response in a traceback.
                flow.terminal = "controller_error"
                flow.append("errors.jsonl", {"time": now(), "code": "CONTROLLER_EXCEPTION", "action_name": action["name"]})
        flow.save()
    if not flow.terminal:
        flow.terminal = "request_budget_exhausted"
    flow.save()
    usage = {**usage_from(records), "schema_errors": schema_errors,
             "wall_time_s": round(time.perf_counter() - started, 3)}
    result = {**score(task, read(directory / "state.json"), usage), **job, "finished_at": now()}
    write(directory / "result.json", result)
    if flow.final:
        f = flow.final
        text = (f"# 初步设计方案（待工程人员审核）\n\n构型依据：{f['configuration_reason']}\n\n"
                f"候选：{f['candidate_id']}；输入版本：{f['input_version']}。\n\n"
                f"计算依据：{f['calculation_basis']}\n\n待核实事项：\n" +
                "\n".join("- " + x for x in f["limitations"]) +
                "\n\n采用的完整输入见 final_design.json；独立评分未回传模型。\n")
        (directory / "design_report.md").write_text(text, encoding="utf-8")
    return result


def run(root, label, allow_paid):
    if not allow_paid:
        raise ValueError("explicit_allow_paid_flag_required")
    folder = root / label
    frozen = verify(folder)  # Verify before reading the credential or making any network request.
    tasks = {x["id"]: x for x in read(folder / "tasks.json")}
    prompts, jobs = read(folder / "prompts.json"), read(folder / "schedule.json")
    config = load_config(PROJECT / "models.toml")
    config["timeout_sec"], config["max_output_tokens"] = 180, 4096
    if public_settings(config) != frozen["model_settings"]:
        raise ValueError("frozen_model_settings_mismatch")
    for index, job in enumerate(jobs):
        verify(folder)
        result = run_one(tasks[job["task_id"]], job, folder, prompts, config)
        say(label=label, completed=index + 1, planned=len(jobs), run_id=job["id"],
            valid=result["valid_design"], terminal=result["terminal"], calls=result["model_requests"])
        summarize(root, label, quiet=True)
        if result["terminal"] in ("controller_error", "service_error"):
            say(status="paused_for_infrastructure_diagnosis", label=label)
            return
    say(status="all_scheduled_runs_complete", label=label, count=len(jobs))


def bootstrap_difference(rows, a, b):
    by_task = {}
    for row in rows:
        by_task.setdefault(row["task_id"], {}).setdefault(row["method"], []).append(int(row["valid_design"]))
    diffs = [statistics.mean(x[a]) - statistics.mean(x[b]) for x in by_task.values() if a in x and b in x]
    if not diffs:
        return None
    rng = random.Random(20261020)
    sample = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(10000))
    return {"task_count": len(diffs), "difference": statistics.mean(diffs),
            "task_cluster_bootstrap_95": [sample[249], sample[9749]],
            "interpretation": "exploratory, task-level resampling; repeated sessions not independent tasks"}


def summarize(root, label, quiet=False):
    folder = root / label
    rows = [read(p) for p in sorted((folder / "runs").glob("*/result.json"))]
    methods = {}
    common = {}
    for row in rows:
        common.setdefault((row["task_id"], row["repeat"]), {})[row["method"]] = row["valid_design"]
    mutually_successful = {key for key, value in common.items() if len(value) == 3 and all(value.values())}
    for method in METHODS:
        group = [x for x in rows if x["method"] == method]
        if not group:
            continue
        methods[method] = {
            "runs": len(group), "valid_designs": sum(x["valid_design"] for x in group),
            "valid_rate": statistics.mean(x["valid_design"] for x in group),
            "by_category": {c: {"runs": sum(x["category"] == c for x in group),
                               "valid": sum(x["category"] == c and x["valid_design"] for x in group)}
                            for c in ("S1", "S2", "S3")},
            "mean_requirement_coverage": statistics.mean(x["requirement_coverage"] for x in group),
            "total_requests": sum(x["model_requests"] for x in group),
            "total_reported_tokens": sum(x["tokens"].get("total_tokens", 0) for x in group),
            "usage_known_responses": sum(x["usage_known_responses"] for x in group),
            "mean_requests": statistics.mean(x["model_requests"] for x in group),
            "mean_wall_time_s": statistics.mean(x["wall_time_s"] for x in group),
            "mean_questions": statistics.mean(x["question_rounds"] for x in group),
            "repeated_confirmation_fields": sum(x["repeated_confirmation_fields"] for x in group),
            "candidate_scenario_calculations": sum(x["counts"].get("candidate_scenario_calculations", 0) for x in group),
            "schema_errors": sum(x["schema_errors"] for x in group),
            "common_success_runs": sum((x["task_id"], x["repeat"]) in mutually_successful for x in group),
            "common_success_mean_requests": None}
        paired = [x for x in group if (x["task_id"], x["repeat"]) in mutually_successful]
        if paired:
            methods[method]["common_success_mean_requests"] = statistics.mean(x["model_requests"] for x in paired)
    summary = {"label": label, "updated_at": now(), "completed_runs": len(rows), "methods": methods,
               "contrasts": {f"{a}-{b}": bootstrap_difference(rows, a, b) for a, b in (("M2", "M0"), ("M2", "M1"))},
               "rows": rows, "limitations": ["scripted engineer replies", "two modeled tracked-chassis configurations",
                      "only tasks with feasible confirmed inputs", "physical validity independent of explanation quality",
                      "latency is client wall time, not isolated model reasoning time",
                      "M0 manual calculations are not counted as zero mental arithmetic work"]}
    write(folder / "summary.json", summary)
    with (folder / "results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["id", "task_id", "repeat", "method", "family", "category", "valid_design", "submitted",
                  "requirement_coverage", "revision_consistent", "question_rounds", "model_requests",
                  "wall_time_s", "schema_errors", "terminal"]
        writer = csv.DictWriter(f, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    text = [f"# {label} 结果汇总", "", f"已完成 {len(rows)} 次运行。", "",
            "|组别|运行|有效方案|平均请求|平均补问|候选×工况计算|", "|---|---:|---:|---:|---:|---:|"]
    for m, x in methods.items():
        text.append(f"|{m}|{x['runs']}|{x['valid_designs']}|{x['mean_requests']:.2f}|{x['mean_questions']:.2f}|{x['candidate_scenario_calculations']}|")
    text.extend(["", "有效方案按冻结输入与独立工程参考判定，不代表报告说明逐句正确或实机性能已验证。",
                 "M0自行文字计算，不将其无外部计算调用解释为零计算工作量。开发结果与正式结果分开。",
                 "服务返回token缺失时仅累计已返回用量；墙钟时间包含网络等待。"])
    (folder / "汇总.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    if not quiet:
        say(status="summarized", label=label, completed=len(rows),
            valid_by_method={m: v["valid_designs"] for m, v in methods.items()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "freeze", "run", "summary", "verify"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--suite", choices=["development", "formal"], default="development")
    parser.add_argument("--label", default="development_01")
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode == "prepare":
            prepare(args.root)
        elif args.mode == "freeze":
            freeze(args.root, args.suite, args.label)
        elif args.mode == "run":
            run(args.root, args.label, args.allow_paid)
        elif args.mode == "summary":
            summarize(args.root, args.label)
        else:
            manifest = verify(args.root / args.label)
            say(status="freeze_verified", freeze_id=manifest["freeze_id"][:12])
    except Exception as exc:
        # No task, credential, request body, or traceback is printed on generic errors.
        say(status="stopped", error_type=type(exc).__name__, code=(
            str(exc) if type(exc) is ValueError and str(exc).replace("_", "").isalnum() else "details_omitted"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
