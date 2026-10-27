"""V4 public-state controller. Never imports tasks, scorer or private reference."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from ..process_comparison.util import now, write, flatten, get, put
from ..process_comparison.runtime import parse, same
from .decision import EvidenceSession, compare_candidates, fixed_decision, digest, physical_case

COMMON_SCHEMA = """
每次只输出一个JSON对象，恰含name、arguments、rationale（非空字符串）三个根字段。
共同动作：
confirm_inputs: arguments={}。确认当前保存的输入和偏好。A组由程序启动固定全目录计算与比较；
  B/C组仅记录确认，不代选工程动作。已确认的原始完整数据无需复制写回。
update_inputs: arguments={"values":{"case.完整路径":值,"policy.priority":["字段名"],...}}。
  只落实公开修改；case.scenarios等数组可按公开修改完整替换。路径前缀case或policy不可省略。
  changed_fields为空表示没有改变。只改policy可继续使用物理输入未变的工程证据，但要按当前policy比较。
ask_engineer: arguments={"fields":["case.路径",...],"question":"待确认事项"}。
  最多2轮；脚本只确认当前阶段已公开事实，不返回选型或未来信息。
continue_design: arguments={"note":"中间比较或解释"}。只保存笔记，不计算，不返回答案。
submit_design: arguments={"candidate_id":"目录候选ID","input_version":当前整数版本,
  "selection_reason":"与当前约束、偏好及工具证据的对应理由",
  "evidence_scope":"实际检查范围和选择依据","limitations":["未覆盖的工程内容"]}。
  提交绑定当前case和policy，结束当前阶段；状态submitted_for_review。
  提交不回传独立评分，不保证选型合格。相同格式对所有组适用。
stop: arguments={"reason":"无法继续的原因"}。结束当前阶段，后续变更仍按统一顺序发布。
每阶段最多24次模型请求，格式纠正计入；不能把脚本确认称为真人批准。
"""
ENGINEERING_SCHEMA = """
本组可自主选择共享工程动作：
assess: arguments={"candidate_ids":null}检查全部当前允许目录；
  或{"candidate_ids":["指定ID",...]}只检查这些候选。可一次批量执行校验、载荷和约束计算。
  已计算且物理依赖未变的候选×工况会自动复用；目录成员资格重新检查。
compare_candidates: arguments={}。根据当前policy比较最近assess得到的候选。
  current_evidence_status注明最近证据是否匹配当前物理输入；范围不足时仅为局部比较。
工具只能返回实际结果，不提供私有评分或未来要求。
"""


def facts(state, prefix=""):
    out = {}
    for key, value in state.items():
        path = f"{prefix}.{key}" if prefix else key
        out[path] = deepcopy(value)
        if isinstance(value, dict):
            out.update(facts(value, path))
    return out


class Controller:
    def __init__(self, public, method, directory):
        self.state = deepcopy(public["initial_state"])
        self.start = deepcopy(public["starting_design"])
        self.method, self.directory = method, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.version = 1
        self.engine = EvidenceSession()
        self.evidence = self.comparison = None
        self.history, self.notes, self.deliveries = [], [], []
        self.counts = Counter()
        self.stage = -1
        self.final = self.terminal = None
        self.public = self.authorized = None
        self.questions = 0
        self.asked = Counter()

    def append(self, name, item):
        with (self.directory / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")

    def begin_stage(self, index, public, external_policy):
        self.stage = index
        self.public = deepcopy(public)
        self.authorized = facts(external_policy["authorized_state"])
        self.final = self.terminal = None
        self.questions, self.asked = 0, Counter()
        self.append("stage_releases.jsonl", {"stage": index, "public": public, "input_version": self.version})
        self.save()

    def snapshot(self):
        return {"state": deepcopy(self.state), "version": self.version,
                "stage": self.stage, "method": self.method, "final": self.final,
                "terminal": self.terminal, "counts": dict(self.counts),
                "work": dict(self.engine.counts), "questions_this_stage": self.questions}

    def save(self):
        write(self.directory / "state.json", self.snapshot())

    def compact_evidence(self):
        if not self.evidence:
            return None
        ev = self.evidence
        if "case_hash" not in ev:
            return ev
        return {k: v for k, v in ev.items() if k != "candidates"} | {
            "candidate_summary": [
                {"candidate_id": r["candidate_id"], "feasible": r["feasible"],
                 "hydraulic_margin": r["hydraulic_margin"],
                 "failure_types": sorted({f["constraint"] for f in r["failures"]})}
                for r in ev["candidates"]],
            "failed_examples": [
                {"candidate_id": r["candidate_id"], "failures": r["failures"][:3]}
                for r in ev["candidates"] if not r["feasible"]][:2]}

    def compact_comparison(self):
        if not self.comparison:
            return None
        return {k: v for k, v in self.comparison.items() if k != "ranked"} | {
            "ranked_top": self.comparison["ranked"][:5]}

    def starting_spec(self):
        return self.start["specification"] if self.start else None

    def engineering(self, name, arguments, source):
        before = dict(self.engine.counts)
        if name == "fixed_decision":
            value = fixed_decision(self.engine, self.state["case"], self.state["policy"], self.starting_spec())
            self.evidence, self.comparison = value["evidence"], value["comparison"]
        elif name == "assess":
            self.evidence = self.engine.assess(self.state["case"], arguments["candidate_ids"])
            self.comparison = None
            value = self.evidence
        else:
            if not self.evidence:
                return {"status": "no_assessment_available"}
            self.comparison = compare_candidates(self.state["case"], self.evidence,
                                                 self.state["policy"], self.starting_spec())
            value = self.comparison
        self.counts["engineering_actions"] += 1
        self.append("engineering_events.jsonl",
                    {"stage": self.stage, "input_version": self.version, "source": source,
                     "name": name, "arguments": arguments, "result": value,
                     "work_before": before, "work_after": dict(self.engine.counts)})
        return {"status": "engineering_result_available",
                "evidence": self.compact_evidence(), "comparison": self.compact_comparison()}

    def execute(self, action):
        name, args = action["name"], action["arguments"]
        try:
            if name == "confirm_inputs" and args == {}:
                result = (self.engineering("fixed_decision", {}, "fixed_controller")
                          if self.method == "A" else {"status": "input_confirmation_recorded"})
            elif name == "update_inputs":
                values = args.get("values")
                if set(args) != {"values"} or not isinstance(values, dict) or not values:
                    raise ValueError("use_nonempty_values_object")
                rejected = [p for p, v in values.items()
                            if p not in self.authorized or not same(v, self.authorized[p])]
                if rejected:
                    self.counts["blocked_updates"] += 1
                    result = {"status": "update_rejected", "fields": rejected,
                              "reason": "not_a_current_public_or_confirmed_value"}
                else:
                    changes = {p: v for p, v in values.items() if not same(get(self.state, p), v)}
                    if changes:
                        for path, value in changes.items():
                            put(self.state, path, value)
                        self.version += 1
                        self.comparison = None
                        self.append("input_revisions.jsonl",
                                    {"stage": self.stage, "input_version": self.version,
                                     "values": changes, "decider": "released_user_requirement",
                                     "executor": "model_update_action"})
                    else:
                        self.counts["no_change_updates"] += 1
                    result = {"status": "input_updated", "changed_fields": list(changes),
                              "input_version": self.version}
                    if self.method == "A":
                        result["fixed_results"] = self.engineering("fixed_decision", {}, "fixed_controller")
            elif name == "ask_engineer":
                if set(args) != {"fields", "question"} or not isinstance(args["fields"], list) or \
                        not args["fields"] or not all(isinstance(p, str) for p in args["fields"]) or \
                        not isinstance(args["question"], str):
                    raise ValueError("use_fields_list_and_question")
                if self.questions >= 2:
                    result = {"status": "question_budget_exhausted"}
                else:
                    self.questions += 1
                    self.counts["question_rounds"] += 1
                    answered, unavailable = {}, []
                    for path in args["fields"]:
                        if path in self.authorized:
                            answered[path] = deepcopy(self.authorized[path])
                            self.counts["repeated_confirmation_fields"] += int(self.asked[path] > 0)
                            self.asked[path] += 1
                        else:
                            unavailable.append(path)
                    result = {"status": "answered", "answers": answered, "not_available": unavailable,
                              "reviewer": "scripted_public_fact_confirmation"}
                    self.append("review_log.jsonl", {"stage": self.stage, "reply": result})
            elif name in ("assess", "compare_candidates") and self.method in ("B", "C"):
                if name == "assess":
                    ids = args.get("candidate_ids")
                    if set(args) != {"candidate_ids"} or (ids is not None and
                        (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids))):
                        raise ValueError("use_candidate_ids_null_or_string_list")
                elif args != {}:
                    raise ValueError("compare_arguments_must_be_empty")
                result = self.engineering(name, args, "model_action")
            elif name == "continue_design":
                if set(args) != {"note"} or not isinstance(args["note"], str):
                    raise ValueError("use_note_string")
                self.notes.append({"stage": self.stage, "note": args["note"]})
                result = {"status": "note_saved"}
            elif name == "submit_design":
                required = {"candidate_id", "input_version", "selection_reason", "evidence_scope", "limitations"}
                if (set(args) != required or type(args["input_version"]) is not int or
                    not all(isinstance(args[k], str) and args[k].strip()
                            for k in ("candidate_id", "selection_reason", "evidence_scope")) or
                    not isinstance(args["limitations"], list) or
                    not all(isinstance(x, str) for x in args["limitations"])):
                    raise ValueError("use_exact_submit_schema")
                if args["input_version"] != self.version:
                    result = {"status": "stale_submission", "current_version": self.version}
                else:
                    self.final = {**deepcopy(args), "adopted_state": deepcopy(self.state),
                                  "stage": self.stage, "status": "submitted_for_review"}
                    self.terminal = "submitted_for_review"
                    result = {"status": self.terminal, "independent_score_returned": False}
                    write(self.directory / f"final_stage_{self.stage}.json", self.final)
            elif name == "stop":
                if set(args) != {"reason"} or not isinstance(args["reason"], str):
                    raise ValueError("use_reason_string")
                self.terminal = "model_stopped"
                result = {"status": self.terminal, "reason": args["reason"]}
            else:
                raise ValueError("action_or_arguments_not_available")
        except ValueError as exc:
            self.counts["invalid_actions"] += 1
            result = {"status": "invalid_action", "code": str(exc)}
        event = {"stage": self.stage, "input_version": self.version,
                 "action": deepcopy(action), "feedback": result}
        self.history.append(event)
        self.append("actions.jsonl", event)
        self.save()
        return result

    def format_error(self, error):
        self.counts["format_errors"] += 1
        event = {"stage": self.stage, "input_version": self.version,
                 "action": {"name": "format_error"},
                 "feedback": {"status": error, "instruction": "请按已提供的动作JSON结构重写。"}}
        self.history.append(event)
        self.append("actions.jsonl", event)

    def context(self, attempts):
        current_hash = digest(physical_case(self.state["case"]))
        return {
            "current_request": self.public, "current_state": self.state,
            "input_version": self.version, "stage_index": self.stage,
            "starting_design": self.start, "prior_deliveries": self.deliveries,
            "notes": self.notes[-4:],
            "recent_actions": [
                {**{k: event[k] for k in ("stage", "input_version", "action")},
                 "feedback": {k: v for k, v in event["feedback"].items()
                              if k not in ("evidence", "comparison", "fixed_results")}}
                for event in self.history[-4:]],
            "engineering_evidence": self.compact_evidence(),
            "preference_comparison": self.compact_comparison(),
            "current_evidence_status": {"matches_current_physical_inputs": bool(
                self.evidence and self.evidence.get("case_hash") == current_hash)},
            "remaining_model_requests": 24 - attempts, "remaining_questions": 2 - self.questions}


def messages(flow, attempts, prompts):
    common = "\n\n".join([prompts["common"], prompts["engineering_spec"],
                           prompts["contract"], COMMON_SCHEMA])
    if flow.method != "A":
        common += "\n\n" + ENGINEERING_SCHEMA
    common += "\n\n" + prompts["A" if flow.method == "A" else "B"]
    if flow.method == "C":
        common += "\n\n" + prompts["skill_body"]
    return [{"role": "system", "content": common},
            {"role": "user", "content": json.dumps(flow.context(attempts), ensure_ascii=False)}]
