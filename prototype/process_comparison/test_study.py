"""Offline regression checks. These scripted tests are not model experiment runs."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from .tasks import build_tasks
from .runtime import Flow, parse, messages
from .evaluate import score
from .study import DEFAULT_ROOT, prompt_bundle, run_one
from .util import flatten, get, read, write
from ..chat_api import ModelServiceError


def act(name, **args):
    return {"name": name, "arguments": args, "rationale": "offline test only"}


class StudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = build_tasks("development")
        cls.formal = build_tasks("formal")
        cls.root = DEFAULT_ROOT / "QA" / uuid4().hex

    def flow(self, task, method):
        return Flow(task["public"], task["external_policy"], method, self.root / uuid4().hex)

    def confirmed_update(self, flow, task):
        if task["external_policy"]["answer_table"]:
            flow.execute(act("ask_engineer", fields=list(task["external_policy"]["answer_table"]), question="offline"))
        expected = flatten(task["private"]["target"])
        changes = {p: v for p, v in expected.items() if get(flow.case, p) != v}
        return flow.execute(act("update_inputs", values=changes))

    def test_all_tasks_crosschecked_and_disjoint(self):
        self.assertEqual(len(self.tasks), 6)
        self.assertEqual(len(self.formal), 12)
        fingerprints = [x["private"]["target_digest"] for x in self.tasks + self.formal]
        self.assertEqual(len(set(fingerprints)), 18)
        self.assertTrue(all(x["private"]["reference"]["feasible_ids"] for x in self.tasks + self.formal))
        self.assertEqual(sum(x["private"]["prior_remains_feasible"] is False for x in self.formal), 2)
        self.assertEqual(sum(x["private"]["prior_remains_feasible"] is True for x in self.formal), 2)

    def test_m0_never_calls_engineering_and_can_continue(self):
        task = self.tasks[0]
        flow = self.flow(task, "M0")
        with patch("prototype.process_comparison.runtime.eng.calculate_loads", side_effect=AssertionError("called")):
            self.assertEqual(flow.execute(act("calculate_loads"))["status"], "action_not_available_in_this_method")
            self.confirmed_update(flow, task)
            for _ in range(3):
                self.assertEqual(flow.execute(act("continue_design", note="manual calculation"))["status"], "note_saved")
        self.assertEqual(flow.counts["engineering_tool_calls"], 0)

    def test_m1_real_fixed_pipeline_does_not_need_reference(self):
        for task in self.tasks:
            flow = self.flow(task, "M1")
            self.assertEqual(flow.counts["engineering_tool_calls"], 0)
            with patch("prototype.process_comparison.reference.reference", side_effect=AssertionError("private reference called")):
                self.confirmed_update(flow, task)
            self.assertEqual(flow.checked, flow.version)
            self.assertEqual(flow.checks["status"], "feasible_within_scope")
            self.assertEqual(flow.matches["check_scope"], "all")

    def test_m2_only_executes_requested_tools_and_enforces_prerequisites(self):
        task = self.tasks[0]
        flow = self.flow(task, "M2")
        self.confirmed_update(flow, task)
        self.assertEqual(flow.counts["engineering_tool_calls"], 0)
        self.assertEqual(flow.execute(act("calculate_loads"))["status"], "precondition_failed")
        for name in ("validate_requirements", "check_configuration", "calculate_loads"):
            self.assertEqual(flow.execute(act(name))["status"], "ok")
        cid = task["private"]["reference"]["ranked_first"]
        self.assertEqual(flow.execute(act("match_hst", scope="candidate", candidate_id=cid))["candidate_count"], 1)
        self.assertEqual(flow.execute(act("check_constraints"))["status"], "feasible_within_scope")
        self.assertEqual(flow.counts["candidate_scenario_calculations"], len(flow.case["scenarios"]))

    def test_answer_not_autofilled_and_unauthorized_update_blocked(self):
        task = self.tasks[1]
        flow = self.flow(task, "M1")
        p, v = next(iter(task["external_policy"]["answer_table"].items()))
        self.assertIsNone(get(flow.case, p))
        self.assertEqual(flow.execute(act("update_inputs", values={p: v}))["status"], "input_review_rejected")
        flow.execute(act("ask_engineer", fields=[p], question="offline"))
        self.assertIsNone(get(flow.case, p))
        flow.execute(act("update_inputs", values={p: v}))
        self.assertEqual(get(flow.case, p), v)
        self.assertEqual(flow.execute(act("update_inputs", values={p: v * 1.1}))["status"], "input_review_rejected")
        self.assertEqual(get(flow.case, p), v)

    def test_question_budget_repeat_and_null_semantics(self):
        task = self.tasks[1]
        flow = self.flow(task, "M0")
        p = next(iter(task["external_policy"]["answer_table"]))
        for _ in range(3):
            flow.execute(act("ask_engineer", fields=[p], question="offline"))
        self.assertEqual(flow.engineer.repeated_confirmations, 2)
        self.assertEqual(flow.execute(act("ask_engineer", fields=[p], question="offline"))["status"], "question_budget_exhausted")
        self.assertEqual(flow.execute(act("update_inputs", values={p: None}))["status"], "input_review_rejected")

    def test_parser_only_syntactic_normalization(self):
        action, err, edits = parse('{"name":"continue_design","arguments":{"note":"x","rationale":"why"}}')
        self.assertIsNone(err)
        self.assertEqual(action["rationale"], "why")
        self.assertEqual(len(edits), 1)
        self.assertIsNotNone(parse('{"name":"continue_design","arguments":{"note":"x"}}')[1])

    def test_common_submission_review_is_noncalculating_and_does_not_fill_values(self):
        task = self.tasks[0]
        for method in ("M0", "M1", "M2"):
            flow = self.flow(task, method)
            old = deepcopy(flow.case)
            with patch("prototype.process_comparison.runtime.eng.calculate_loads", side_effect=AssertionError("called")):
                result = flow.execute(act("submit_design", candidate_id="arbitrary",
                    input_version=flow.version, configuration_reason="x", calculation_basis="x", limitations=[]))
            self.assertEqual(result["status"], "submission_form_incomplete")
            self.assertEqual(flow.case, old)
            self.assertNotIn("answers", result)

    def test_public_context_excludes_private_answers_and_reference(self):
        task = deepcopy(self.tasks[1])
        task["private"]["secret_sentinel"] = "NEVER_SEND_REFERENCE_SENTINEL"
        flow = self.flow(task, "M2")
        content = json.dumps(messages(flow, task["id"], 0, prompt_bundle()), ensure_ascii=False)
        self.assertNotIn("NEVER_SEND_REFERENCE_SENTINEL", content)
        self.assertNotIn('"answer_table"', content)
        self.assertNotIn('"ranked_first"', content)
        self.assertNotIn('"private"', content)
        self.assertEqual(flow.engineer.confirmed_answers, {})

    def test_scoring_accepts_any_feasible_choice_and_rejects_stale_input(self):
        task = self.tasks[-1]
        flow = self.flow(task, "M0")
        self.confirmed_update(flow, task)
        ref = task["private"]["reference"]
        for cid in (ref["feasible_ids"][-1], next(k for k, v in ref["candidates"].items() if not v["feasible"])):
            flow.execute(act("submit_design", candidate_id=cid, input_version=flow.version,
                              configuration_reason="offline", calculation_basis="offline", limitations=[]))
            result = score(task, read(flow.directory / "state.json"), {})
            self.assertEqual(result["valid_design"], cid in ref["feasible_ids"])
        state = read(flow.directory / "state.json")
        state["final"]["candidate_id"] = ref["feasible_ids"][0]
        state["final"]["case"] = deepcopy(task["public"]["case"])
        self.assertFalse(score(task, state, {})["valid_design"])
        self.assertEqual(flow.execute(act("submit_design", candidate_id=ref["feasible_ids"][0],
            input_version=flow.version - 1, configuration_reason="x", calculation_basis="x", limitations=[]))["status"], "stale_submission")

    def test_transport_retry_and_format_error_count_as_requests(self):
        task = self.tasks[0]
        answer = json.dumps(act("stop", reason="offline"))
        outputs = [ModelServiceError("NETWORK_OR_TIMEOUT; details omitted"),
                   {"response": {"content": "invalid", "finish_reason": "stop", "usage": {"total_tokens": 4}}, "latency_s": 0},
                   {"response": {"content": answer, "finish_reason": "stop", "usage": {"total_tokens": 5}}, "latency_s": 0}]
        with patch("prototype.process_comparison.study.complete", side_effect=outputs):
            result = run_one(task, {"id": "TEST", "method": "M0", "task_id": task["id"], "repeat": 1},
                             self.root / uuid4().hex, prompt_bundle(), {})
        self.assertEqual(result["model_requests"], 3)
        self.assertEqual(result["schema_errors"], 1)
        self.assertEqual(result["service_error_requests"], 1)
        self.assertEqual(result["tokens"]["total_tokens"], 9)
        self.assertFalse(result["valid_design"])

    def test_36_request_cap_includes_format_corrections(self):
        task = self.tasks[0]
        with patch("prototype.process_comparison.study.complete",
                   return_value={"response": {"content": "invalid", "finish_reason": "stop", "usage": {}}}) as mock:
            result = run_one(task, {"id": "BUDGET", "method": "M0", "task_id": task["id"], "repeat": 1},
                             self.root / uuid4().hex, prompt_bundle(), {})
        self.assertEqual(mock.call_count, 36)
        self.assertEqual(result["model_requests"], 36)
        self.assertEqual(result["terminal"], "request_budget_exhausted")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StudyTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    write(DEFAULT_ROOT / "offline_tests.json", {"status": "passed" if result.wasSuccessful() else "failed",
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "scope": "offline deterministic integration and protocol regression; not API outcomes"})
    raise SystemExit(0 if result.wasSuccessful() else 1)
