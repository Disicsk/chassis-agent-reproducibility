"""Public-input engineering evidence, explicit preferences and exact cache reuse.

All experimental methods must receive the same functions. This is a tool patch,
not an autonomous agent, a new hydraulic model, or the independent trial scorer.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math

from .. import engineering as eng
from ..process_comparison.util import candidates


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def physical_case(case):
    keys = ("design_family", "requirements", "efficiencies", "design_limits",
            "planetary", "scenarios", "illustrative_catalog")
    return {k: deepcopy(case[k]) for k in keys if k in case}


def spec_order(family):
    if family == "dual_power_stream":
        return ("drive_pump_ml_rev", "drive_motor_ml_rev",
                "steering_pump_ml_rev", "steering_motor_ml_rev")
    return ("pump_ml_rev", "motor_ml_rev")


class EvidenceSession:
    """One cache per design run, shared only across that run's revisions.

    Cache units are candidate × scenario; keys include every physical input
    section plus the candidate specification. Catalog membership, input validity
    and configuration applicability are rechecked on every call.
    """
    def __init__(self):
        self.cache = {}
        self.counts = Counter()

    def assess(self, case, candidate_ids=None):
        if case.get("design_family") not in ("independent_hst", "dual_power_stream"):
            return {"status": "unsupported_model", "candidates": []}
        validation = eng.validate_requirements(case)
        if validation["status"] != "ok":
            return {"status": "input_not_ready", "validation": validation,
                    "candidates": []}
        # Accept the engineering validator's unit normalization, never fill gaps.
        case = validation["normalized"]
        family = case["design_family"]
        configuration = eng.check_configuration(family, case["requirements"], family)
        if configuration["status"] != "ok":
            return {"status": "configuration_not_applicable",
                    "configuration": configuration, "candidates": []}
        ids = [s["id"] for s in case["scenarios"]]
        if len(set(ids)) != len(ids):
            raise ValueError("scenario_ids_must_be_unique")
        catalog = dict(candidates(case))
        selected = list(catalog) if candidate_ids is None else list(candidate_ids)
        if not selected or len(set(selected)) != len(selected):
            raise ValueError("candidate_scope_must_be_nonempty_and_unique")
        if any(cid not in catalog for cid in selected):
            raise ValueError("candidate_not_in_current_allowed_catalog")
        before = self.counts.copy()
        inputs = physical_case(case)
        rows = []
        for cid in selected:
            spec = catalog[cid]
            scenarios = []
            for scenario in case["scenarios"]:
                dependencies = {k: v for k, v in inputs.items()
                                if k not in ("scenarios", "illustrative_catalog")}
                key = digest({"inputs": dependencies, "scenario": scenario,
                              "specification": spec})
                if key not in self.cache:
                    single = deepcopy(case)
                    single["scenarios"] = [deepcopy(scenario)]
                    loads = eng.calculate_loads(single)
                    args = ({"branch_selection": spec} if family == "dual_power_stream"
                            else {"pump": spec["pump_ml_rev"],
                                  "motor": spec["motor_ml_rev"]})
                    matched = eng.match_hst(single, loads, **args)
                    checked = eng.check_constraints(single, matched["candidates"])
                    result = checked["candidates"][0]
                    if result["candidate_id"] != cid:
                        raise ValueError("tool_candidate_identity_mismatch")
                    self.cache[key] = deepcopy(result["scenarios"][0])
                    self.counts["computed_candidate_scenarios"] += 1
                else:
                    self.counts["reused_candidate_scenarios"] += 1
                scenarios.append(deepcopy(self.cache[key]))
            failures = [
                {"scenario_id": s["scenario_id"], "constraint": k, **item}
                for s in scenarios for k, item in s["checks"].items()
                if not item["passed"]
            ]
            pressure_flow = [
                1 - item["required"] / item["available"]
                for s in scenarios for key, item in s["checks"].items()
                if (key in ("pressure", "flow") or
                    key.endswith(("_pressure", "_flow")))
            ]
            rows.append({"candidate_id": cid, "specification": deepcopy(spec),
                         "scenarios": scenarios, "failures": failures,
                         "feasible": not failures,
                         "hydraulic_margin": min(pressure_flow)})
        feasible = [r["candidate_id"] for r in rows if r["feasible"]]
        return {
            "status": ("feasible_within_scope" if feasible else "no_feasible_within_scope"),
            "case_hash": digest(inputs), "design_family": family,
            "scope": "all" if set(selected) == set(catalog) else "subset",
            "evaluated_ids": selected, "catalog_count": len(catalog),
            "feasible_ids": feasible, "candidates": rows,
            "work": {k: self.counts[k] - before[k] for k in
                     ("computed_candidate_scenarios", "reused_candidate_scenarios")},
            "cache_policy": "exact physical dependencies; catalog checked separately",
        }


OBJECTIVES = {
    "hydraulic_margin": "max",
    "size_index": "min",
    "changed_spec_positions": "min",
}


def compare_candidates(case, evidence, policy, starting_spec=None):
    """Rank assessed feasible candidates by a publicly confirmed preference.

    Ranking over a subset is explicitly local. Never claims global optimality
    without complete current-catalog evidence. No silent default preferences.
    """
    normalized = eng.validate_requirements(case)
    if normalized["status"] != "ok":
        raise ValueError("current_input_not_ready")
    case = normalized["normalized"]
    if evidence.get("case_hash") != digest(physical_case(case)):
        raise ValueError("stale_or_incompatible_evidence")
    if set(policy) != {"priority", "minimum_hydraulic_margin"}:
        raise ValueError("explicit_priority_and_minimum_margin_required")
    priorities = policy["priority"]
    if (not isinstance(priorities, list) or not priorities or
            any(not isinstance(p, str) or p not in OBJECTIVES for p in priorities) or
            len(set(priorities)) != len(priorities)):
        raise ValueError("invalid_priority")
    minimum = policy["minimum_hydraulic_margin"]
    if (isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or
            not math.isfinite(minimum) or not 0 <= minimum < 1):
        raise ValueError("minimum_margin_must_be_finite_in_0_1")
    order = spec_order(case["design_family"])
    if "changed_spec_positions" in priorities or starting_spec is not None:
        if not isinstance(starting_spec, dict) or set(starting_spec) != set(order):
            raise ValueError("same_family_starting_specification_required")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or v <= 0 for v in starting_spec.values()):
            raise ValueError("invalid_starting_specification")
    catalog = dict(candidates(case))
    expected = evidence.get("evaluated_ids")
    rows = evidence.get("candidates", [])
    if (not isinstance(expected, list) or not rows or
            len(set(expected)) != len(expected) or
            [r["candidate_id"] for r in rows] != expected or
            any(cid not in catalog for cid in expected)):
        raise ValueError("inconsistent_evidence_scope")
    maxima = {k: max(s[k] for s in catalog.values()) for k in order}
    ranked, rejected = [], []
    for row in rows:
        cid, spec = row["candidate_id"], catalog[row["candidate_id"]]
        if spec != row["specification"]:
            raise ValueError("candidate_specification_mismatch")
        if not row["feasible"] or row["hydraulic_margin"] + 1e-10 < minimum:
            rejected.append(cid)
            continue
        metrics = {
            "hydraulic_margin": row["hydraulic_margin"],
            "size_index": sum(spec[k] / maxima[k] for k in order) / len(order),
            "changed_spec_positions": (sum(spec[k] != starting_spec[k] for k in order)
                                       if starting_spec is not None else None),
        }
        # Shared numeric tie tolerance; all equally ranked candidates are accepted.
        key = tuple(round(metrics[p] * (-1 if OBJECTIVES[p] == "max" else 1), 10)
                    for p in priorities)
        ranked.append({"candidate_id": cid, "metrics": metrics, "rank_key": key})
    ranked.sort(key=lambda r: (r["rank_key"], r["candidate_id"]))
    best_ids = [r["candidate_id"] for r in ranked
                if r["rank_key"] == ranked[0]["rank_key"]] if ranked else []
    coverage = set(expected) == set(catalog)
    return {
        "status": "ranked" if ranked else "no_eligible_in_assessed_scope",
        "case_hash": evidence["case_hash"], "policy_hash": digest(policy),
        "policy": deepcopy(policy), "ranked": ranked, "best_ids": best_ids,
        "rejected_ids": rejected, "global_catalog_coverage": coverage,
        "optimality_scope": "current_allowed_catalog" if coverage else "assessed_subset_only",
        "metric_limits": "size_index is not cost, mass or efficiency; margin is not a dynamic safety certification",
    }


def fixed_decision(session, case, policy, starting_spec=None):
    """Strong fixed baseline: same evidence, cache and preference ranking.

    It computes/checks the entire currently allowed catalog. LLM use is needed
    only to parse/clarify inputs and present the result in an eventual runner.
    """
    evidence = session.assess(case)
    if not evidence.get("case_hash"):
        return {"evidence": evidence, "comparison": None}
    return {"evidence": evidence,
            "comparison": compare_candidates(case, evidence, policy, starting_spec)}
