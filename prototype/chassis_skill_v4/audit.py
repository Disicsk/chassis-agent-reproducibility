"""Reconstruct every request/action from public stages; no API calls."""
from collections import Counter
from copy import deepcopy
import json

from ..process_comparison.util import read, write
from ..process_comparison.study import usage_from
from .runtime import Controller, messages, parse
from .evaluate import score_stage
from .study import verify, say, settings


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def clean_events(rows):
    return [{k:v for k,v in row.items() if k != "time"} for row in rows]


def audit(root, label):
    folder = root / label
    frozen = verify(folder)
    tasks = {t["id"]: t for t in read(folder / "tasks.json")}
    prompt = read(folder / "prompts.json")
    schedule = read(folder / "schedule.json")
    reports = []
    secret = settings()["api_key"]
    for job in schedule:
        directory = folder / "runs" / job["id"]
        if not (directory / "result.json").exists():
            continue
        task, saved = tasks[job["task_id"]], read(directory / "result.json")
        requests = [read(p) for p in sorted(directory.glob("request_*.json"))]
        responses = [read(p) for p in sorted(directory.glob("response_*.json"))]
        if len(requests) != len(responses):
            raise ValueError("request_response_count_mismatch")
        scratch = folder / "audit_scratch" / job["id"]
        # Repeated audits use new scratch subdirectories; never append to previous replay.
        i = 1
        while (scratch / str(i)).exists():
            i += 1
        flow = Controller(task["public"], job["method"], scratch / str(i))
        checked = 0
        for index, stage_saved in enumerate(saved["stages"]):
            stage = task["stages"][index]
            flow.begin_stage(index, stage["public"], stage["external_policy"])
            before = flow.engine.counts.copy()
            pairs = [(q,r) for q,r in zip(requests,responses) if q["stage"] == index]
            prior_context = None
            for n, (request, response) in enumerate(pairs):
                expected = messages(flow, n, prompt) if request["transport_attempt"] == 0 else prior_context
                if expected != request["messages"]:
                    raise ValueError("public_context_reconstruction_mismatch")
                prior_context = expected
                if response.get("request") and response["request"]["messages"] != expected:
                    raise ValueError("actual_request_message_mismatch")
                if secret in json.dumps({"request": request, "response": response}, ensure_ascii=False):
                    raise ValueError("credential_found_in_log")
                if response.get("error"):
                    if n == len(pairs)-1:
                        flow.terminal = "service_error"
                    continue
                action, error, edits = (parse(response["response"]["content"])
                                       if response["response"]["finish_reason"] == "stop"
                                       else (None, "incomplete_action_output", []))
                if error:
                    flow.format_error(error)
                else:
                    flow.execute(action)
                checked += 1
            if not flow.terminal:
                flow.terminal = "request_budget_exhausted"
            snapshot = flow.snapshot()
            if snapshot != read(directory / f"state_stage_{index}.json"):
                raise ValueError("replayed_state_mismatch")
            usage = {**usage_from([r for _,r in pairs]), "wall_time_s": stage_saved["wall_time_s"],
                     "work": {k:flow.engine.counts[k]-before[k] for k in
                              ("computed_candidate_scenarios","reused_candidate_scenarios")}}
            if score_stage(task,index,snapshot,usage) != stage_saved:
                raise ValueError("independent_score_mismatch")
            flow.deliveries.append({"stage": index, "terminal": flow.terminal, "final": flow.final})
        for name in ("engineering_events.jsonl","input_revisions.jsonl","actions.jsonl","review_log.jsonl"):
            if clean_events(jsonl(directory/name)) != clean_events(jsonl(flow.directory/name)):
                raise ValueError("replayed_tool_or_review_log_mismatch")
        for k,v in usage_from(responses).items():
            if saved[k] != v:
                raise ValueError("usage_summary_mismatch")
        reports.append({"id":job["id"],"requests":len(requests),"replayed_actions":checked,
                        "current_stage_inputs_only":True,"scores_recomputed":True,
                        "tools_replayed":True,"credential_in_logs":False})
    status = "passed" if len(reports) == len(schedule) else "incomplete"
    result = {"status":status,"label":label,"runs":len(reports),"expected_runs":len(schedule),
              "requests":sum(r["requests"] for r in reports),"freeze_id":frozen["freeze_id"],
              "coverage":"saved local requests, outputs, tool/review events, states and independent numerical scoring",
              "not_verified":["provider internals","full prose semantic correctness","human engineering approval"],
              "rows":reports}
    write(folder/"audit.json",result)
    say(status="audit_"+status,label=label,runs=len(reports),requests=result["requests"])
    return result
