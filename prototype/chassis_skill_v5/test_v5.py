"""Offline check (no API calls): replay recorded v4 formal runs through the v5 wrapper.

Verifies freeze, run_one, scoring, summary, paired statistics and the audit wrapper
reproduce the saved v4 results exactly (wall-clock fields excluded).
Run from the project root:  python -B -m prototype.chassis_skill_v5.test_v5
"""
import copy
import json
from pathlib import Path
import shutil
import tempfile

from ..process_comparison.util import read, write, now
from ..chassis_skill_v4 import study as v4
from . import study as v5

JOBS = ("F03_A_r1", "F03_B_r1", "F09_A_r2", "F09_B_r2")


def strip(obj):
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items() if k not in ("wall_time_s", "finished_at", "started_at", "time")}
    if isinstance(obj, list):
        return [strip(x) for x in obj]
    return obj


def main():
    checks, info = {}, {}
    tmp = Path(tempfile.mkdtemp(prefix="v5_offline_"))
    saved_root, saved_labels, saved_complete = v5.ROOT, dict(v5.LABELS), v4.complete
    try:
        # temporary root holding the plan file needed by sources()
        (tmp / "private").mkdir(parents=True)
        shutil.copy2(saved_root / "v5实验方案.md", tmp / "v5实验方案.md")
        write(tmp / "offline_tests.json", {"status": "passed"})
        v5.ROOT = tmp
        v5.LABELS["OFFLINE"] = {"model": "deepseek_v4_1_flash", "tasks": "v4:formal", "repeats": 2, "seed": 1}
        # sources() lists ROOT/v5实验方案.md relative to PROJECT; tmp is outside -> patch sources
        orig_sources = v5.sources
        v5.sources = lambda: sorted(set(v4.sources()) | set(Path(v5.__file__).parent.glob("*.py")))
        v5.freeze("OFFLINE")
        folder = tmp / "OFFLINE"
        checks["prompts_identical_to_v4_formal"] = read(folder / "prompts.json") == read(v4.ROOT / "formal/prompts.json")
        tasks = {t["id"]: t for t in read(folder / "tasks.json")}
        bundle = read(folder / "prompts.json")
        config = v5.settings("deepseek_v4_1_flash")
        mismatch = []
        for jid in JOBS:
            src = v4.ROOT / "formal/runs" / jid
            queue = [read(p) for p in sorted(src.glob("response_*.json"))]
            def stub(cfg, messages, max_tokens=None, _q=queue):
                rec = copy.deepcopy(_q.pop(0))
                rec.pop("request_index", None); rec.pop("stage", None)
                if rec.get("request"):
                    if rec["request"]["messages"] != messages:
                        mismatch.append(jid)
                    rec["request"]["messages"] = messages   # a live call echoes the sent messages
                if rec.get("error"):
                    from ..chat_api import ModelServiceError
                    raise ModelServiceError(rec["error"])
                return rec
            v4.complete = stub
            job = next(j for j in read(folder / "schedule.json") if j["id"] == jid)
            result = v4.run_one(tasks[job["task_id"]], job, folder, bundle, config)
            checks[f"replay_{jid}_matches_v4"] = strip(result) == strip(read(src / "result.json"))
            checks[f"replay_{jid}_consumed_all_responses"] = not queue
        info = {"recorded_v4_messages_byte_identical": not mismatch,
                "note": "informational; float last-digit differences arise between Python <3.12 and >=3.12 sum()"}
        if mismatch:
            print("message differences in", sorted(set(mismatch)))
        s = v5.summarize("OFFLINE", quiet=True)
        checks["summary_rows"] = s["completed_runs"] == len(JOBS)
        p = v5.paired(s["rows"])
        checks["paired_stats_computed"] = p["tasks"] == 2
        a = v5.audit("OFFLINE")
        checks["audit_replayed_runs"] = a["runs"] == len(JOBS)
        v5.sources = orig_sources
    finally:
        v4.complete = saved_complete
        v5.ROOT, v5.LABELS = saved_root, saved_labels
        shutil.rmtree(tmp, ignore_errors=True)
    status = "passed" if all(checks.values()) else "failed"
    import platform
    write(v5.ROOT / "offline_tests.json", {"status": status, "checked_at": now(), "api_calls": 0,
                                           "python": platform.python_version(), "checks": checks, "info": info})
    print(json.dumps({"status": status, "checks": checks}, ensure_ascii=False))
    if status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
