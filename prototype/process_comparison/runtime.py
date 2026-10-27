"""Experiment controllers. No reference-solution imports or private target access."""
import json
import math
from collections import Counter
from copy import deepcopy
from pathlib import Path
from .. import engineering as eng
from .util import now, flatten, get, put, candidates, write

COMMON_ACTIONS = {"update_inputs", "ask_engineer", "continue_design", "submit_design", "stop"}
TOOLS = {"validate_requirements", "check_configuration", "calculate_loads", "match_hst", "check_constraints"}

SCHEMA = """
每轮只输出一个JSON对象：{"name":"动作名","arguments":{...},"rationale":"本轮依据"}。
name、arguments、rationale为三个根字段；rationale是非空字符串。不要Markdown或额外包装。
通用动作：
update_inputs: arguments={"values":{"完整.字段.路径":值,...}}。数组用数字下标，如scenarios.0.speed_m_s。
  只能填写题面/附件已公开的值或工程师已经答复的值；构型design_family允许选择independent_hst或dual_power_stream。
  未给出/未确认的值不能用典型值、null、零或猜测补齐。null不是删除或保持不变指令，未修改项不传。
  已在当前case内的值无需再写回。需求中的新值必须写入，报告会绑定提交时的当前case。
ask_engineer: arguments={"fields":["完整.字段.路径",...],"question":"待确认问题"}。
  fields应指向所需参数；也可用其上级路径询问该部分。最多3轮，答复后需自行update_inputs落实答复。
continue_design: arguments={"note":"中间推导、比较或自检内容"}。可以多轮；不会获得工程计算答案。
submit_design: arguments={"candidate_id":"按目录语法填写","input_version":当前版本整数,
  "configuration_reason":"构型与接口对应依据","calculation_basis":"关键计算及各适用约束的检查依据",
  "limitations":["尚未覆盖的设计内容",...]}。
  该动作提交最终待工程人员审核的初步方案，结束本次运行；不会返回参考答案或工程评分。
  提交时自动附上当前case（不补值）作为明确采用的输入。不能提交旧版本结果。
  提交前必须把文字里已给定和补问已确认的参数通过update_inputs写入当前case。
  三组均接受同样的表单完整性检查；有空缺时只提示缺项，不填值、不计算也不判断候选可行性。
stop: arguments={"reason":"尚未解决的原因"}，结束本次运行。
共用审核：程序模拟工程师核对输入值是否有公开依据，并按冻结答复表回答问题；
这不等于真人工程师审核，也不替代最终独立工程评分。提交状态统一为submitted_for_review。
"""
TOOL_SCHEMA = """
本组另可选择工程动作：
validate_requirements: arguments={}，只校验当前已写入输入。
check_configuration: arguments={}，检查当前design_family及转向能力，前提为当前输入校验通过。
calculate_loads: arguments={}，前提为当前构型检查通过。
match_hst: arguments={"scope":"all"}或{"scope":"candidate","candidate_id":"目录内ID"}，
  前提为当前载荷已计算；all枚举当前允许目录，candidate只计算指定一项。
check_constraints: arguments={}，检查最近一次当前版本的候选匹配结果；未检查的其他候选不受此结论覆盖。
任何输入改变后，本轮输入、构型和计算结果的有效标记均失效；历史结果仅供参考。
"""


def schema(method):
    return SCHEMA + (TOOL_SCHEMA if method == "M2" else "\n本组不接受模型显式调用工程动作。")


def same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9)
    return a == b


def parse(text):
    """A logged syntactic normalization; never invent arguments or engineering facts."""
    try:
        action = json.loads(text)
    except (ValueError, TypeError):
        return None, "invalid_json", []
    edits = []
    if not isinstance(action, dict):
        return None, "root_must_be_object", edits
    args = action.get("arguments")
    if "rationale" not in action and isinstance(args, dict) and isinstance(args.get("rationale"), str):
        action["rationale"] = args.pop("rationale")
        edits.append("move_unique_nested_rationale_to_root")
    if set(action) != {"name", "arguments", "rationale"} or not isinstance(args, dict):
        return None, "required_root_keys_name_arguments_rationale", edits
    if not isinstance(action["name"], str) or not isinstance(action["rationale"], str) or not action["rationale"].strip():
        return None, "name_and_nonempty_rationale_must_be_strings", edits
    return action, None, edits


class Engineer:
    """Public-fact authorization and scripted answers, not a design solver."""
    def __init__(self, policy):
        self.authorized = deepcopy(policy["initial_authorized"])
        self.answers = deepcopy(policy["answer_table"])
        self.rounds = 0
        self.asked = Counter()
        self.repeated_confirmations = 0
        self.confirmed_answers = {}

    def ask(self, fields, question):
        if self.rounds >= 3:
            return {"status": "question_budget_exhausted"}
        self.rounds += 1
        reply, unknown = {}, []
        for p in fields:
            available = {**self.authorized, **self.answers}
            paths = [k for k in available if k == p or k.startswith(p + ".")]
            if not paths:
                unknown.append(p)
            for key in paths:
                self.repeated_confirmations += int(self.asked[key] > 0)
                self.asked[key] += 1
                reply[key] = available[key]
                self.authorized[key] = available[key]
                self.confirmed_answers[key] = available[key]
        return {"status": "answered", "answers": reply, "not_available": unknown,
                "reviewer": "frozen_scripted_engineer", "question": question}

    def review(self, values):
        rejected = []
        for p, v in values.items():
            if p == "design_family":
                if v not in ("independent_hst", "dual_power_stream"):
                    rejected.append(p)
            elif p not in self.authorized or v is None or not same(v, self.authorized[p]):
                rejected.append(p)
        return rejected


class Flow:
    def __init__(self, public, policy, method, directory):
        self.public = deepcopy(public)
        self.method = method
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.engineer = Engineer(policy)
        self.case = deepcopy(public["case"])
        self.version = 1
        self.results = {}
        self.history = []
        self.notes = []
        self.final = None
        self.terminal = None
        self.counts = Counter()
        self.validated = self.configured = self.loaded = self.matched = self.checked = None
        self.loads = self.matches = self.checks = None
        self.save()

    def append(self, filename, item):
        with (self.directory / filename).open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")

    def save(self):
        write(self.directory / "state.json", {
            "method": self.method, "case": self.case, "input_version": self.version,
            "terminal": self.terminal, "final": self.final, "counts": dict(self.counts),
            "question_rounds": self.engineer.rounds,
            "repeated_confirmation_fields": self.engineer.repeated_confirmations,
            "confirmed_answers": self.engineer.confirmed_answers})

    def invalidate(self):
        self.validated = self.configured = self.loaded = self.matched = self.checked = None
        self.loads = self.matches = self.checks = None
        self.results = {}

    def compact(self, result):
        if "candidates" not in result:
            return {k: v for k, v in result.items() if k != "normalized"}
        rows = result["candidates"]
        out = {k: v for k, v in result.items() if k != "candidates"}
        out["candidate_count"] = len(rows)
        out["candidate_ids"] = [x["candidate_id"] for x in rows]
        out["candidate_details"] = rows if len(rows) <= 1 else []
        if any("passes_modeled_checks" in x for x in rows):
            out["feasible_ids"] = [x["candidate_id"] for x in rows if x["passes_modeled_checks"]]
            out["failed_examples"] = [x for x in rows if not x["passes_modeled_checks"]][:2]
        return out

    def tool(self, name, args, source):
        self.counts["engineering_tool_calls"] += 1
        if name == "validate_requirements":
            if self.case.get("design_family") not in ("independent_hst", "dual_power_stream"):
                result = {"status": "missing_input", "missing": ["design_family"]}
            else:
                result = eng.validate_requirements(self.case)
            if result["status"] == "ok":
                self.validated = self.version
        elif name == "check_configuration":
            if self.validated != self.version:
                result = {"status": "precondition_failed", "required_fact": "current_input_validated"}
            else:
                result = eng.check_configuration(self.case["design_family"], self.case["requirements"], self.case["design_family"])
                if result["status"] == "ok":
                    self.configured = self.version
        elif name == "calculate_loads":
            if self.configured != self.version:
                result = {"status": "precondition_failed", "required_fact": "current_configuration_checked"}
            else:
                result = eng.calculate_loads(self.case)
                self.loads, self.loaded = result, self.version
        elif name == "match_hst":
            if self.loaded != self.version:
                result = {"status": "precondition_failed", "required_fact": "current_loads_available"}
            else:
                scope = args.get("scope")
                choices = dict(candidates(self.case))
                if scope not in ("all", "candidate") or (scope == "candidate" and args.get("candidate_id") not in choices):
                    result = {"status": "invalid_arguments", "reason": "scope_all_or_catalog_candidate_required"}
                else:
                    kw = {}
                    if scope == "candidate":
                        spec = choices[args["candidate_id"]]
                        kw = ({"branch_selection": spec} if self.case["design_family"] == "dual_power_stream"
                              else {"pump": spec["pump_ml_rev"], "motor": spec["motor_ml_rev"]})
                    result = eng.match_hst(self.case, self.loads, **kw)
                    result["check_scope"] = scope
                    self.matches, self.matched = result, self.version
                    self.checked, self.checks = None, None
                    self.counts["candidate_scenario_calculations"] += len(result["candidates"]) * len(self.case["scenarios"])
        else:
            if self.matched != self.version:
                result = {"status": "precondition_failed", "required_fact": "current_candidates_available"}
            else:
                result = eng.check_constraints(self.case, self.matches["candidates"])
                result["check_scope"] = self.matches["check_scope"]
                self.checks, self.checked = result, self.version
                self.counts["candidate_scenario_constraint_checks"] += len(result["candidates"]) * len(self.case["scenarios"])
        self.append("engineering_events.jsonl", {"time": now(), "source": source, "name": name,
                    "arguments": args, "input_version": self.version, "result": result})
        self.results[name] = self.compact(result)
        return self.results[name]

    def fixed_pipeline(self):
        assert self.method == "M1"
        for name in ("validate_requirements", "check_configuration", "calculate_loads", "match_hst", "check_constraints"):
            result = self.tool(name, {"scope": "all"} if name == "match_hst" else {}, "fixed_controller")
            if result["status"] not in ("ok", "feasible_within_scope"):
                break

    def execute(self, action):
        name, args = action["name"], action["arguments"]
        if name not in COMMON_ACTIONS | (TOOLS if self.method == "M2" else set()):
            result = {"status": "action_not_available_in_this_method"}
        elif name == "update_inputs":
            values = args.get("values")
            if not isinstance(values, dict) or not values or any(not isinstance(k, str) for k in values):
                result = {"status": "invalid_arguments", "reason": "nonempty_flat_path_values_required"}
            else:
                rejected = self.engineer.review(values)
                if rejected:
                    self.counts["blocked_input_update_actions"] += 1
                    result = {"status": "input_review_rejected", "fields": rejected,
                              "reason": "not_a_public_or_confirmed_value; input_unchanged"}
                else:
                    changes = {p: v for p, v in values.items() if not same(get(self.case, p), v)}
                    if changes:
                        for p, v in changes.items():
                            put(self.case, p, v)
                        self.version += 1
                        self.invalidate()
                        self.append("input_revisions.jsonl", {"time": now(), "input_version": self.version,
                                   "values": changes, "decider": "public_user_fact_or_scripted_reply",
                                   "executor": "model_update_action"})
                        if self.method == "M1":
                            self.fixed_pipeline()
                    else:
                        self.counts["no_change_updates"] += 1
                    result = {"status": "input_review_accepted", "input_version": self.version,
                              "changed_fields": list(changes), "reviewer": "frozen_scripted_engineer"}
        elif name == "ask_engineer":
            fields = args.get("fields")
            if not isinstance(fields, list) or not fields or not all(isinstance(p, str) and p for p in fields) or not isinstance(args.get("question"), str):
                result = {"status": "invalid_arguments", "reason": "fields_list_and_question_required"}
            else:
                result = self.engineer.ask(fields, args["question"])
                self.append("review_log.jsonl", {"time": now(), "input_version": self.version, "reply": result})
        elif name == "continue_design":
            if not isinstance(args.get("note"), str) or not args["note"].strip():
                result = {"status": "invalid_arguments", "reason": "nonempty_note_required"}
            else:
                self.notes.append(args["note"])
                result = {"status": "note_saved", "engineering_answer_returned": False}
        elif name == "submit_design":
            needed = {"candidate_id", "input_version", "configuration_reason", "calculation_basis", "limitations"}
            valid = (set(args) == needed and all(isinstance(args[k], str) and args[k].strip()
                        for k in ("candidate_id", "configuration_reason", "calculation_basis"))
                     and type(args["input_version"]) is int and isinstance(args["limitations"], list)
                     and all(isinstance(x, str) for x in args["limitations"]))
            if not valid:
                result = {"status": "invalid_arguments", "reason": "use_exact_submit_design_schema"}
            elif args["input_version"] != self.version:
                result = {"status": "stale_submission", "current_input_version": self.version}
            else:
                # Common form review uses public field declarations, not the private target,
                # no values are supplied and no equations or candidate checks are run.
                fields = set(self.engineer.authorized) | set(self.engineer.answers)
                missing = sorted(p for p in fields if p.startswith(
                    ("requirements.", "scenarios.", "planetary.", "efficiencies.", "design_limits."))
                    and get(self.case, p) is None)
                if self.case.get("design_family") is None:
                    missing.append("design_family")
                if missing:
                    self.counts["incomplete_submission_forms"] += 1
                    result = {"status": "submission_form_incomplete", "missing_fields": missing,
                              "engineering_score_returned": False}
                else:
                    self.final = {**deepcopy(args), "case": deepcopy(self.case), "status": "submitted_for_review"}
                    self.terminal = "submitted_for_review"
                    write(self.directory / "final_design.json", self.final)
                    result = {"status": self.terminal, "engineering_score_returned": False}
        elif name == "stop":
            if not isinstance(args.get("reason"), str) or not args["reason"].strip():
                result = {"status": "invalid_arguments", "reason": "nonempty_reason_required"}
            else:
                self.terminal = "model_stopped"
                result = {"status": "model_stopped", "reason": args["reason"]}
        else:
            result = self.tool(name, args, "model_action")
        if result["status"] in ("precondition_failed", "invalid_arguments", "action_not_available_in_this_method"):
            self.counts["invalid_or_premature_actions"] += 1
        event = {"time": now(), "input_version": self.version, "action": action, "feedback": result}
        self.history.append(event)
        self.append("actions.jsonl", event)
        self.save()
        return result

    def context(self, requests):
        return {
            "current_case": self.case, "input_version": self.version,
            "confirmed_answers": self.engineer.confirmed_answers,
            "prior_notes": self.notes,
            "last_actions": self.history[-8:],
            "actual_engineering_results": self.results,
            "current_version_status": {
                "input_validated": self.validated == self.version,
                "configuration_checked": self.configured == self.version,
                "loads_calculated": self.loaded == self.version,
                "candidates_calculated": self.matched == self.version,
                "constraints_checked": self.checked == self.version,
                "report_status": "not_submitted" if not self.terminal else self.terminal},
            "remaining_requests": 36 - requests, "remaining_questions": 3 - self.engineer.rounds}


def messages(flow, tid, requests, prompts):
    system = prompts["common"].replace("{{ENGINEERING_SPEC}}", prompts["engineering_spec"])
    system = system.replace("{{CATALOG_AND_PREFERENCES}}",
        "元件目录和公开偏好见当前case.illustrative_catalog及requirements.configuration_preference；没有隐藏目标或指定答案。")
    system = system.replace("{{ACTION_AND_DELIVERY_SCHEMA}}", schema(flow.method)) + "\n" + prompts[flow.method]
    ctx = flow.context(requests)
    substitutions = {
        "TASK_ID": tid, "CURRENT_USER_REQUEST": flow.public["request"],
        "CONFIRMED_DESIGN_INFORMATION": json.dumps({k: ctx[k] for k in ("current_case", "input_version", "confirmed_answers")}, ensure_ascii=False),
        "PROVIDED_STARTING_DESIGN": json.dumps(flow.public["starting_design"], ensure_ascii=False),
        "OBSERVED_HISTORY_AND_RESULTS": json.dumps({k: ctx[k] for k in ("prior_notes", "last_actions", "actual_engineering_results", "current_version_status")}, ensure_ascii=False),
        "METHOD_STAGE": ("固定调度结果已显示；继续处理输入或据真实结果组织方案。" if flow.method == "M1"
                         else "依据本组动作接口自行决定；当前状态仅记录事实，不含下一步建议。"),
        "REMAINING_BUDGET": json.dumps({k: ctx[k] for k in ("remaining_requests", "remaining_questions")})}
    user = prompts["task_message"]
    for key, value in substitutions.items():
        user = user.replace("{{" + key + "}}", value)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
