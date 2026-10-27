"""Offline check for the S-group addendum (no API calls): replay recorded v4 formal
method-C runs through the addendum freeze + v4.run_one + audit and compare results."""
import copy, json, platform, shutil, tempfile
from pathlib import Path
from ..process_comparison.util import read, write, now
from ..chassis_skill_v4 import study as v4
from . import study as v5
from . import supplement_s as s

JOBS = ("F03_C_r1", "F09_C_r2")


def strip(o):
    if isinstance(o, dict): return {k: strip(v) for k, v in o.items() if k not in ("wall_time_s", "finished_at", "started_at", "time")}
    if isinstance(o, list): return [strip(x) for x in o]
    return o


def main():
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="v5s_offline_"))
    saved = (v5.ROOT, s.ROOT, dict(s.ADD), v4.complete, s.sources)
    try:
        write(tmp / "offline_tests_S.json", {"status": "passed"})
        v5.ROOT = s.ROOT = tmp
        s.ADD["OFFLINE_C"] = {"model": "deepseek_v4_1_flash", "tasks": "v4:formal", "repeats": 2, "seed": 1}
        v5.LABELS["OFFLINE_C"] = s.ADD["OFFLINE_C"]
        s.sources = lambda: sorted(set(v4.sources()) | set(Path(s.__file__).parent.glob("*.py")))
        s.freeze("OFFLINE_C")
        folder = tmp / "OFFLINE_C"
        checks["prompts_identical_to_v4_formal"] = read(folder / "prompts.json") == read(v4.ROOT / "formal/prompts.json")
        checks["schedule_only_method_C"] = all(j["method"] == "C" for j in read(folder / "schedule.json"))
        tasks = {t["id"]: t for t in read(folder / "tasks.json")}
        bundle, config = read(folder / "prompts.json"), v5.settings("deepseek_v4_1_flash")
        for jid in JOBS:
            src = v4.ROOT / "formal/runs" / jid
            queue = [read(p) for p in sorted(src.glob("response_*.json"))]
            def stub(cfg, messages, max_tokens=None, _q=queue):
                r = copy.deepcopy(_q.pop(0)); r.pop("request_index", None); r.pop("stage", None)
                if r.get("request"): r["request"]["messages"] = messages
                return r
            v4.complete = stub
            job = next(j for j in read(folder / "schedule.json") if j["id"] == jid)
            res = v4.run_one(tasks[job["task_id"]], job, folder, bundle, config)
            checks[f"replay_{jid}_matches_v4"] = strip(res) == strip(read(src / "result.json"))
            checks[f"replay_{jid}_consumed_all"] = not queue
        checks["summary_ok"] = s.summarize("OFFLINE_C")["completed_runs"] == len(JOBS)
        checks["audit_ok"] = v5.audit("OFFLINE_C")["runs"] == len(JOBS)
    finally:
        v5.ROOT, s.ROOT, add, v4.complete, s.sources = saved
        s.ADD.clear(); s.ADD.update(add); v5.LABELS.pop("OFFLINE_C", None)
        shutil.rmtree(tmp, ignore_errors=True)
    status = "passed" if all(checks.values()) else "failed"
    write(s.ROOT / "offline_tests_S.json", {"status": status, "checked_at": now(), "api_calls": 0,
                                           "python": platform.python_version(), "checks": checks})
    print(json.dumps({"status": status, "checks": checks}))
    if status != "passed": raise SystemExit(1)


if __name__ == "__main__":
    main()
