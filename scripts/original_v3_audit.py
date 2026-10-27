"""Read-only API evidence audit; deterministic replay writes only into audit_scratch."""
import json
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT))
from prototype.process_comparison.util import read, write, sha, now
from prototype.process_comparison.runtime import Flow, messages, parse
from prototype.process_comparison.study import verify, usage_from
from prototype.process_comparison.evaluate import score
from prototype.chat_api import load_config


def strip_times(x):
    if isinstance(x, dict):
        return {k: strip_times(v) for k, v in x.items() if k != "time"}
    if isinstance(x, list):
        return [strip_times(v) for v in x]
    return x


def normalized_messages(items):
    # Task messages contain embedded JSON blocks, including action timestamps.
    import re
    return [re.sub(r'"time":\s*"[^"]+"', '"time":"<timestamp>"', x["content"]) for x in items]


def audit(label):
    folder = ROOT / label
    frozen = verify(folder)
    tasks = {x["id"]: x for x in read(folder / "tasks.json")}
    prompts = read(folder / "prompts.json")
    jobs = read(folder / "schedule.json")
    snapshots_ok = all(sha(folder / "source_snapshot" / p) == h for p, h in frozen["files"].items())
    audit_root = ROOT / "audit_scratch" / (label + "_" + uuid4().hex)
    rows = []
    for job in jobs:
        d = folder / "runs" / job["id"]
        if not (d / "result.json").exists():
            continue
        task = tasks[job["task_id"]]
        flow = Flow(task["public"], task["external_policy"], job["method"], audit_root / job["id"])
        records, context_errors, last_messages, format_errors = [], [], None, 0
        for request_path in sorted(d.glob("request_*.json")):
            request = read(request_path)
            index = request["request_index"]
            expected = (last_messages if request["transport_attempt"] else messages(flow, task["id"], len(records), prompts))
            if normalized_messages(request["messages"]) != normalized_messages(expected):
                context_errors.append(index)
            last_messages = request["messages"]
            response_path = d / f"response_{index:03d}.json"
            if not response_path.exists():
                context_errors.append(f"missing_response_{index}")
                continue
            record = read(response_path)
            records.append(record)
            if "error" in record:
                continue
            if record["response"]["finish_reason"] != "stop":
                action, error = None, "incomplete_action_output"
            else:
                action, error, _ = parse(record["response"]["content"])
            if error:
                format_errors += 1
                flow.history.append({"time": now(), "input_version": flow.version, "action": {"name": "format_error"},
                    "feedback": {"status": error, "instruction": "按系统提示的动作JSON结构重写；不要工程工具或额外包装。"}})
            else:
                flow.execute(action)
        state, result = read(d / "state.json"), read(d / "result.json")
        derived = score(task, state, {})
        usage = usage_from(records)
        events = [json.loads(x) for x in (d / "engineering_events.jsonl").read_text(encoding="utf-8").splitlines()] if (d / "engineering_events.jsonl").exists() else []
        replay_events = [json.loads(x) for x in (flow.directory / "engineering_events.jsonl").read_text(encoding="utf-8").splitlines()] if (flow.directory / "engineering_events.jsonl").exists() else []
        final = state.get("final")
        final_evidence = None
        if final and job["method"] != "M0":
            final_evidence = any(
                e["name"] == "check_constraints" and e["input_version"] == final["input_version"]
                and any(c["candidate_id"] == final["candidate_id"] and c.get("passes_modeled_checks")
                        for c in e["result"].get("candidates", []))
                for e in events)
        rows.append({
            "id": job["id"], "method": job["method"], "category": task["category"],
            "context_allowlist_replay_equal": not context_errors, "context_error_request_indices": context_errors,
            "engineering_events_replay_equal": strip_times(events) == strip_times(replay_events),
            "final_input_replay_equal": state["case"] == flow.case,
            "final_submission_replay_equal": state["final"] == flow.final,
            "independent_score_equal": all(derived[k] == result[k] for k in derived),
            "usage_equal": all(usage[k] == result[k] for k in usage),
            "request_budget_respected": len(records) <= 36,
            "question_budget_respected": state["question_rounds"] <= 3,
            "format_error_count_equal": format_errors == result["schema_errors"],
            "final_candidate_has_current_passing_tool_evidence": final_evidence,
            "tool_sources": dict(Counter(x["source"] for x in events)),
            "m0_no_engineering_calls": not events if job["method"] == "M0" else None,
            "m1_no_agent_tool_selection": all(x["source"] == "fixed_controller" for x in events) if job["method"] == "M1" else None,
            "m2_no_fixed_controller": all(x["source"] == "model_action" for x in events) if job["method"] == "M2" else None})
    # Credential is used for an equality scan only; never serialized or printed.
    secret = load_config(PROJECT / "models.toml")["api_key"]
    scanned, credential_found = 0, False
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".jsonl", ".txt", ".md", ".csv", ".py"):
            scanned += 1
            if secret in p.read_text(encoding="utf-8-sig"):
                credential_found = True
    checks = ["context_allowlist_replay_equal", "engineering_events_replay_equal", "final_input_replay_equal",
              "final_submission_replay_equal", "independent_score_equal", "usage_equal",
              "request_budget_respected", "question_budget_respected", "format_error_count_equal"]
    all_ok = snapshots_ok and not credential_found and all(all(r[k] for k in checks) for r in rows)
    report = {"time": now(), "label": label, "status": "passed" if all_ok else "review_required",
              "scheduled": len(jobs), "completed": len(rows), "source_snapshots_verified": snapshots_ok,
              "credential_scan_files": scanned, "credential_found": credential_found,
              "rows": rows, "scope": "all saved API messages replayed from public task, own actions and scripted replies; no paid API",
              "limits": ["does not verify provider internals or training contamination",
                         "does not certify every natural-language calculation/explanation",
                         "timeout requests may have executed server-side; missing usage is not estimated"]}
    write(folder / "audit.json", report)
    print(json.dumps({"label": label, "status": report["status"], "completed": len(rows),
                      "scheduled": len(jobs), "context_mismatch_runs": sum(not r["context_allowlist_replay_equal"] for r in rows),
                      "credential_found": credential_found}), flush=True)


if __name__ == "__main__":
    audit(sys.argv[1])
