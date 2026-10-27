"""Offline behavioral checks on public examples, not v4 formal task outcomes."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from .decision import EvidenceSession, compare_candidates, fixed_decision, spec_order
from .reference_metrics import expected_choices
from ..process_comparison.reference import reference
from ..process_comparison.util import candidates


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def example(family):
    name = "rice_wheat.json" if family == "independent_hst" else "rapeseed.json"
    result = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    result["design_family"] = family
    return result


def default_policy():
    return {"priority": ["size_index", "hydraulic_margin"],
            "minimum_hydraulic_margin": 0}


class DecisionTests(unittest.TestCase):
    def assert_reference_checks(self, case, observed):
        expected = reference(case)
        self.assertEqual(set(observed["feasible_ids"]), set(expected["feasible_ids"]))
        for row in observed["candidates"]:
            other = expected["candidates"][row["candidate_id"]]
            self.assertEqual(row["feasible"], other["feasible"])
            for scenario in row["scenarios"]:
                for name, check in scenario["checks"].items():
                    ref = other["checks"][scenario["scenario_id"]][name]
                    self.assertEqual(check["passed"], ref["passed"])
                    for field in ("required", "available"):
                        self.assertAlmostEqual(check[field], ref[field], places=7)

    def test_both_models_match_independent_equations(self):
        for family in ("independent_hst", "dual_power_stream"):
            with self.subTest(family=family):
                case = example(family)
                evidence = EvidenceSession().assess(case)
                self.assert_reference_checks(case, evidence)

    def test_preference_order_matches_independent_reference(self):
        for family in ("independent_hst", "dual_power_stream"):
            case = example(family)
            evidence = EvidenceSession().assess(case)
            starting = dict(candidates(case))[evidence["feasible_ids"][0]]
            for priority in (["size_index", "hydraulic_margin"],
                             ["hydraulic_margin", "size_index"],
                             ["changed_spec_positions"],
                             ["changed_spec_positions", "hydraulic_margin"]):
                with self.subTest(family=family, priority=priority):
                    policy = {"priority": priority, "minimum_hydraulic_margin": 0}
                    observed = compare_candidates(case, evidence, policy, starting)
                    ordered = tuple(starting[k] for k in spec_order(family))
                    expected = expected_choices(case, priority, 0, ordered)
                    self.assertEqual(sorted(observed["best_ids"]), expected["best_ids"])
                    self.assertTrue(observed["global_catalog_coverage"])

    def test_second_assessment_reuses_all_physical_results(self):
        case, session = example("independent_hst"), EvidenceSession()
        first = session.assess(case)
        second = session.assess(case)
        total = len(dict(candidates(case))) * len(case["scenarios"])
        self.assertEqual(first["work"]["computed_candidate_scenarios"], total)
        self.assertEqual(second["work"]["computed_candidate_scenarios"], 0)
        self.assertEqual(second["work"]["reused_candidate_scenarios"], total)
        self.assertEqual(first["candidates"], second["candidates"])

    def test_changed_scenario_recomputes_only_that_scenario(self):
        case, session = example("dual_power_stream"), EvidenceSession()
        session.assess(case)
        case["scenarios"][0]["speed_m_s"] *= 1.05
        observed = session.assess(case)
        count = len(dict(candidates(case)))
        self.assertEqual(observed["work"]["computed_candidate_scenarios"], count)
        self.assertEqual(observed["work"]["reused_candidate_scenarios"],
                         count * (len(case["scenarios"]) - 1))
        self.assert_reference_checks(case, observed)

    def test_global_physical_changes_invalidate_all_scenarios(self):
        for section, field in (("requirements", "working_mass_kg"),
                               ("requirements", "pump_speed_rpm"),
                               ("efficiencies", "motor_mechanical"),
                               ("design_limits", "working_pressure_limit_mpa")):
            with self.subTest(section=section, field=field):
                case, session = example("independent_hst"), EvidenceSession()
                session.assess(case)
                case[section][field] *= .97
                observed = session.assess(case)
                total = len(dict(candidates(case))) * len(case["scenarios"])
                self.assertEqual(observed["work"]["computed_candidate_scenarios"], total)
                self.assertEqual(observed["work"]["reused_candidate_scenarios"], 0)
                self.assert_reference_checks(case, observed)

    def test_preference_change_reranks_without_new_engineering(self):
        case, session = example("independent_hst"), EvidenceSession()
        evidence = session.assess(case)
        original_counts = dict(session.counts)
        for priority in (["size_index"], ["hydraulic_margin"]):
            p = {"priority": priority, "minimum_hydraulic_margin": 0}
            result = compare_candidates(case, evidence, p)
            expected = expected_choices(case, priority, 0)
            self.assertEqual(sorted(result["best_ids"]), expected["best_ids"])
        self.assertEqual(dict(session.counts), original_counts)

    def test_stale_evidence_rejected(self):
        case = example("independent_hst")
        evidence = EvidenceSession().assess(case)
        case["scenarios"][0]["speed_m_s"] *= 1.1
        with self.assertRaisesRegex(ValueError, "stale"):
            compare_candidates(case, evidence, default_policy())

    def test_catalog_removal_rechecks_membership(self):
        case, session = example("independent_hst"), EvidenceSession()
        evidence = session.assess(case)
        old_id = evidence["candidates"][0]["candidate_id"]
        case["illustrative_catalog"]["pump_displacements_ml_rev"].pop(0)
        with self.assertRaisesRegex(ValueError, "current_allowed_catalog"):
            session.assess(case, [old_id])
        current = session.assess(case)
        self.assertEqual(current["work"]["computed_candidate_scenarios"], 0)
        self.assertNotIn(old_id, current["evaluated_ids"])
        with self.assertRaisesRegex(ValueError, "stale"):
            compare_candidates(case, evidence, default_policy())
        self.assert_reference_checks(case, current)

    def test_subset_never_claims_global_optimality(self):
        case = example("independent_hst")
        cid = reference(case)["feasible_ids"][0]
        evidence = EvidenceSession().assess(case, [cid])
        result = compare_candidates(case, evidence, default_policy())
        self.assertFalse(result["global_catalog_coverage"])
        self.assertEqual(result["optimality_scope"], "assessed_subset_only")

    def test_no_feasible_result_does_not_get_ranked(self):
        case = example("independent_hst")
        case["requirements"]["travel_shaft_power_budget_kw"] = .01
        evidence = EvidenceSession().assess(case)
        result = compare_candidates(case, evidence, default_policy())
        self.assertEqual(result["best_ids"], [])
        self.assertEqual(result["status"], "no_eligible_in_assessed_scope")

    def test_missing_input_not_filled(self):
        case = example("independent_hst")
        del case["requirements"]["working_mass_kg"]
        session = EvidenceSession()
        self.assertEqual(session.assess(case)["status"], "input_not_ready")
        self.assertEqual(dict(session.counts), {})
        self.assertNotIn("working_mass_kg", case["requirements"])

    def test_model_scope_is_explicit(self):
        case = example("independent_hst")
        case["design_family"] = "wheel_unknown"
        self.assertEqual(EvidenceSession().assess(case)["status"], "unsupported_model")

    def test_current_input_is_not_mutated(self):
        case = example("dual_power_stream")
        saved = deepcopy(case)
        fixed_decision(EvidenceSession(), case, default_policy())
        self.assertEqual(case, saved)

    def test_fixed_baseline_has_identical_tools_and_cache(self):
        case = example("dual_power_stream")
        session = EvidenceSession()
        first = fixed_decision(session, case, default_policy())
        second = fixed_decision(session, case, default_policy())
        self.assertEqual(first["comparison"]["best_ids"], second["comparison"]["best_ids"])
        self.assertEqual(second["evidence"]["work"]["computed_candidate_scenarios"], 0)
        self.assertTrue(second["comparison"]["global_catalog_coverage"])

    def test_no_default_or_nonfinite_preferences(self):
        case = example("independent_hst")
        evidence = EvidenceSession().assess(case)
        for policy in ({}, {"priority": [], "minimum_hydraulic_margin": 0},
                       {"priority": ["size_index"], "minimum_hydraulic_margin": float("nan")},
                       {"priority": ["cost"], "minimum_hydraulic_margin": 0}):
            with self.assertRaises(ValueError):
                compare_candidates(case, evidence, policy)

    def test_margin_threshold_applies_to_all_groups(self):
        case = example("dual_power_stream")
        evidence = EvidenceSession().assess(case)
        policy = {"priority": ["size_index"], "minimum_hydraulic_margin": .1}
        observed = compare_candidates(case, evidence, policy)
        expected = expected_choices(case, policy["priority"], .1)
        self.assertEqual(sorted(observed["best_ids"]), expected["best_ids"])
        self.assertTrue(all(r["metrics"]["hydraulic_margin"] >= .1 - 1e-10
                            for r in observed["ranked"]))

    def test_wrong_family_starting_spec_is_rejected(self):
        case = example("independent_hst")
        evidence = EvidenceSession().assess(case)
        dual_spec = next(candidates(example("dual_power_stream")))[1]
        with self.assertRaisesRegex(ValueError, "same_family"):
            compare_candidates(case, evidence, default_policy(), dual_spec)

    def test_mutating_returned_evidence_does_not_corrupt_cache(self):
        case, session = example("independent_hst"), EvidenceSession()
        evidence = session.assess(case)
        evidence["candidates"][0]["scenarios"][0]["checks"]["pressure"]["required"] = -99
        self.assert_reference_checks(case, session.assess(case))


if __name__ == "__main__":
    unittest.main()
