"""Offline group isolation, staged task and scoring tests, without model access."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4
import unittest
from unittest.mock import patch

from .runtime import Controller, messages
from .study import prompts, ROOT
from .evaluate import score_stage
from .tasks import atomic_changes
from .test_decision import example, default_policy
from .decision import EvidenceSession, compare_candidates
from .reference_metrics import expected_choices
from ..process_comparison.reference import reference


def action(name, **arguments):
    return {"name":name,"arguments":arguments,"rationale":"offline interface check"}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        ROOT.joinpath("QA/tmp").mkdir(parents=True,exist_ok=True)
        # Normal workspace mkdir avoids Windows temporary-directory ACL changes.
        self.temp_path = ROOT / "QA" / "runtime_checks" / uuid4().hex
        self.temp_path.mkdir(parents=True)
        self.state = {"case":example("independent_hst"),"policy":default_policy()}
        self.public = {"initial_state":self.state,"starting_design":None}

    def controller(self, method):
        flow=Controller(self.public,method,self.temp_path/method)
        flow.begin_stage(0,{"request":"initial","changes":{}},{"authorized_state":self.state})
        return flow

    def test_only_C_gets_skill(self):
        bundle=prompts()
        for method in ("A","B","C"):
            ctx=messages(self.controller(method),0,bundle)
            self.assertEqual(bundle["skill_body"] in ctx[0]["content"], method=="C")
            self.assertIn(bundle["contract"],ctx[0]["content"])

    def test_no_future_context(self):
        flow=self.controller("C")
        flow.public["future_secret_marker"]="THIS_FIELD_WOULD_BE_PUBLIC"
        # Runtime accepts only released public stages, never an entire task/reference.
        del flow.public["future_secret_marker"]
        encoded=json.dumps(messages(flow,0,prompts()))
        self.assertNotIn("THIS_FIELD_WOULD_BE_PUBLIC",encoded)
        self.assertNotIn('"private"',encoded)

    def test_fixed_pipeline_and_agent_tools_agree(self):
        fixed=self.controller("A")
        fixed.execute(action("confirm_inputs"))
        agent=self.controller("B")
        agent.execute(action("assess",candidate_ids=None))
        agent.execute(action("compare_candidates"))
        self.assertEqual(fixed.evidence,agent.evidence)
        self.assertEqual(fixed.comparison,agent.comparison)
        self.assertEqual(fixed.engine.counts,agent.engine.counts)

    def test_B_C_same_actions_same_results(self):
        b,c=self.controller("B"),self.controller("C")
        for act in (action("assess",candidate_ids=None),action("compare_candidates")):
            self.assertEqual(b.execute(act),c.execute(act))

    def test_revision_not_implicitly_applied(self):
        flow=self.controller("C")
        target=deepcopy(self.state)
        target["policy"]["priority"]=["hydraulic_margin"]
        changes=atomic_changes(self.state,target)
        flow.begin_stage(1,{"request":"change","changes":changes},{"authorized_state":target})
        self.assertNotEqual(flow.state,target)
        flow.execute(action("update_inputs",values=changes))
        self.assertEqual(flow.state,target)
        self.assertEqual(flow.version,2)

    def test_unconfirmed_change_blocked(self):
        flow=self.controller("C")
        before=deepcopy(flow.state)
        feedback=flow.execute(action("update_inputs",values={"case.requirements.working_mass_kg":-1}))
        self.assertEqual(feedback["status"],"update_rejected")
        self.assertEqual(flow.state,before)

    def test_preferences_only_reuse_evidence(self):
        flow=self.controller("C")
        flow.execute(action("assess",candidate_ids=None))
        before=flow.engine.counts.copy()
        target=deepcopy(self.state)
        target["policy"]["priority"]=["hydraulic_margin"]
        change=atomic_changes(self.state,target)
        flow.begin_stage(1,{"request":"change","changes":change},{"authorized_state":target})
        flow.execute(action("update_inputs",values=change))
        flow.execute(action("compare_candidates"))
        self.assertEqual(flow.engine.counts,before)
        self.assertEqual(flow.comparison["policy"],target["policy"])

    def test_failed_stage_can_continue_without_reference_answer(self):
        flow=self.controller("B")
        flow.execute(action("stop",reason="offline test"))
        flow.begin_stage(1,{"request":"next","changes":{}},{"authorized_state":self.state})
        self.assertIsNone(flow.terminal)
        self.assertIsNone(flow.final)

    def test_submission_does_not_return_score(self):
        flow=self.controller("C")
        result=flow.execute(action("submit_design",candidate_id="unknown",
             input_version=1,selection_reason="placeholder",evidence_scope="none",limitations=[]))
        self.assertEqual(result["status"],"submitted_for_review")
        self.assertFalse(result["independent_score_returned"])

    def test_two_question_limit(self):
        flow=self.controller("C")
        act=action("ask_engineer",fields=["policy"],question="confirm")
        self.assertEqual(flow.execute(act)["status"],"answered")
        self.assertEqual(flow.execute(act)["status"],"answered")
        self.assertEqual(flow.execute(act)["status"],"question_budget_exhausted")

    def test_independent_scoring_requires_current_policy(self):
        flow=self.controller("A")
        flow.execute(action("confirm_inputs"))
        candidate=flow.comparison["best_ids"][0]
        flow.execute(action("submit_design",candidate_id=candidate,input_version=1,
                            selection_reason="basis",evidence_scope="all",limitations=[]))
        ref={"engineering":reference(self.state["case"]),
             "quality":expected_choices(self.state["case"],self.state["policy"]["priority"],0)}
        task={"stages":[{"private":{"target":deepcopy(self.state),"reference":ref}}]}
        self.assertTrue(score_stage(task,0,flow.snapshot(),{})["valid"])
        task["stages"][0]["private"]["target"]["policy"]["priority"]=["hydraulic_margin"]
        self.assertFalse(score_stage(task,0,flow.snapshot(),{})["valid"])

    def test_three_stage_runner_with_public_tool_driven_stub(self):
        from .study import run_one
        from ..process_comparison.util import read, get
        task=read(ROOT/"private/tasks_development.json")[0]
        def stub(config, msg, max_tokens):
            context=json.loads(msg[-1]["content"])
            changes=context["current_request"]["changes"]
            pending={p:v for p,v in changes.items() if get(context["current_state"],p)!=v}
            if pending:
                chosen=action("update_inputs",values=pending)
            elif not context["engineering_evidence"]:
                chosen=(action("confirm_inputs") if "本组采用固定工程流程" in msg[0]["content"]
                        else action("assess",candidate_ids=None))
            elif not context["preference_comparison"]:
                chosen=(action("confirm_inputs") if "本组采用固定工程流程" in msg[0]["content"]
                        else action("compare_candidates"))
            else:
                chosen=action("submit_design",candidate_id=context["preference_comparison"]["best_ids"][0],
                              input_version=context["input_version"],selection_reason="actual tool ranking",
                              evidence_scope="current allowed catalog",limitations=["offline stub"])
            return {"request":{"messages":msg},"response":{"finish_reason":"stop","model":"OFFLINE_STUB",
                    "content":json.dumps(chosen),"usage":{"total_tokens":0}}}
        with patch("prototype.chassis_skill_v4.study.complete",side_effect=stub):
            for method in ("A","B","C"):
                job={"id":method,"method":method,"task_id":task["id"],"repeat":1}
                result=run_one(task,job,self.temp_path,prompts(),{})
                self.assertTrue(result["all_stages_valid"])
                self.assertTrue(result["all_stages_preferred"])
                self.assertEqual(len(result["stages"]),3)

    def test_stage_budgets_count_every_request(self):
        from .study import run_one
        from ..process_comparison.util import read
        task=read(ROOT/"private/tasks_development.json")[0]
        def idle(config,msg,max_tokens):
            return {"response":{"finish_reason":"stop","model":"OFFLINE_STUB",
                    "content":json.dumps(action("continue_design",note="idle")),
                    "usage":{"total_tokens":0}}}
        job={"id":"budget","method":"B","task_id":task["id"],"repeat":1}
        with patch("prototype.chassis_skill_v4.study.complete",side_effect=idle):
            result=run_one(task,job,self.temp_path,prompts(),{})
        self.assertEqual(result["model_requests"],72)
        self.assertEqual([s["model_requests"] for s in result["stages"]],[24,24,24])
        self.assertFalse(result["all_stages_valid"])


if __name__=="__main__":
    unittest.main()
