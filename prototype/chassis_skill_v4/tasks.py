"""Seeded v4 task construction; no model-outcome-based selection."""
from copy import deepcopy
from pathlib import Path
import random

from ..process_comparison.tasks import vary, path_for, crosscheck
from ..process_comparison.util import flatten, get, put, candidates
from .decision import digest, physical_case, spec_order, EvidenceSession, compare_candidates
from .reference_metrics import expected_choices


def policy(priority, minimum=0):
    return {"priority": list(priority), "minimum_hydraulic_margin": minimum}


def reference_stage(case, pref, start):
    ref = crosscheck(case)
    if ref is None:
        return None
    values = tuple(start[k] for k in spec_order(case["design_family"])) if start else None
    quality = expected_choices(case, pref["priority"], pref["minimum_hydraulic_margin"], values)
    if not quality["best_ids"]:
        return None
    # Shared tool and independently implemented objective must agree before release.
    ev = EvidenceSession().assess(case)
    observed = compare_candidates(case, ev, pref, start)
    if sorted(observed["best_ids"]) != quality["best_ids"]:
        raise ValueError("independent_preference_crosscheck_failed")
    return {"engineering": ref, "quality": quality}


def atomic_changes(before, after):
    """Objects recurse; arrays are explicit, authorized whole-value replacements."""
    out = {}
    def visit(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                out[path] = deepcopy(b)
            else:
                for k in b:
                    visit(a[k], b[k], f"{path}.{k}" if path else k)
        elif a != b:
            out[path] = deepcopy(b)
    visit(before, after, "")
    return out


def make_task(tid, family, category, variant, rng):
    for attempt in range(600):
        initial = vary(family, rng)
        initial["case_id"], initial["title"] = tid, "底盘初步设计任务"
        base_ref = crosscheck(initial)
        if not base_ref or len(base_ref["feasible_ids"]) < 3:
            continue
        start_id = base_ref["ranked_first"] if category != "T1" else None
        starting = dict(candidates(initial))[start_id] if start_id else None
        cases = [deepcopy(initial) for _ in range(3)]
        if category == "T1":
            preferences = [policy(["size_index", "hydraulic_margin"]),
                           policy(["hydraulic_margin", "size_index"]),
                           policy(["size_index", "hydraulic_margin"])]
            maximized = expected_choices(initial, ["hydraulic_margin"], 0)
            max_margin = max(x["values"]["hydraulic_margin"]
                             for x in maximized["eligible"].values())
            if max_margin <= .04:
                continue
            preferences[2]["minimum_hydraulic_margin"] = round(max_margin * .6, 6)
        elif category == "T2":
            preferences = [policy(["changed_spec_positions"]) for _ in range(3)]
            speed = path_for(initial, "transfer_flat", "speed_m_s")
            put(cases[1], speed, round(get(initial, speed) * (.94 if variant == 1 else .89), 4))
            cases[2] = deepcopy(cases[1])
            # Only one additional scenario changes; unchanged cases are reusable.
            work = path_for(initial, "work_flat", "speed_m_s")
            put(cases[2], work, round(get(initial, work) * .92, 4))
        else:
            preferences = [policy(["changed_spec_positions", "size_index"]) for _ in range(3)]
            speed = path_for(initial, "transfer_flat", "speed_m_s")
            found = False
            for mult in (1.12, 1.22, 1.35, 1.50, 1.70, 1.9):
                changed = deepcopy(initial)
                put(changed, speed, round(get(initial, speed) * mult, 4))
                changed["requirements"]["travel_shaft_power_budget_kw"] = round(
                    min(initial["requirements"]["engine_rated_power_kw"] * .95,
                        initial["requirements"]["travel_shaft_power_budget_kw"] * 1.08), 3)
                check = crosscheck(changed)
                if check and check["feasible_ids"] and start_id not in check["feasible_ids"]:
                    cases[1], found = changed, True
                    break
            if not found:
                continue
            cases[2] = deepcopy(cases[1])
            catalog = cases[2]["illustrative_catalog"]
            pumps = (catalog["drive"]["pump_displacements_ml_rev"]
                     if family == "dual_power_stream" else catalog["pump_displacements_ml_rev"])
            if len(pumps) <= 1:
                continue
            pumps.pop(0)
            if variant == 2:
                cases[2]["requirements"]["working_mass_kg"] = round(
                    cases[2]["requirements"]["working_mass_kg"] * 1.025)
        refs = [reference_stage(c, p, starting) for c, p in zip(cases, preferences)]
        if any(r is None for r in refs):
            continue
        if category == "T1":
            best_sets = [set(r["quality"]["best_ids"]) for r in refs]
            if best_sets[0] == best_sets[1] or best_sets[0] == best_sets[2]:
                continue
        if category == "T2" and any(start_id not in r["quality"]["best_ids"] for r in refs):
            continue
        break
    else:
        raise ValueError("physical_task_construction_exhausted")
    public_start = ({"candidate_id": start_id, "specification": starting,
                     "source": "common supplied feasible starting specification"}
                    if starting else None)
    stages = []
    states = [{"case": c, "policy": p} for c, p in zip(cases, preferences)]
    for i, (state, ref) in enumerate(zip(states, refs)):
        updates = {} if i == 0 else atomic_changes(states[i-1], state)
        request = ("请在给定目录内形成履带式联合收获机底盘初步方案，落实明确的约束和选型优先级。"
                   if i == 0 else "工程师发布本阶段的新要求，请将已公布的修改落实到当前设计信息，并提交当前条件下的方案。")
        stages.append({
            "public": {"request": request, "changes": updates},
            "external_policy": {"authorized_state": deepcopy(state)},
            "private": {"target": deepcopy(state), "reference": ref,
                        "physical_hash": digest(physical_case(state["case"]))},
        })
    return {
        "id": tid, "family": family, "category": category, "variant": variant,
        "public": {"initial_state": states[0], "starting_design": public_start},
        "stages": stages, "construction_attempts": attempt + 1,
    }


def build_tasks(suite):
    rng = random.Random(202610204004 if suite == "formal" else 202610204003)
    rows = []
    for family in ("independent_hst", "dual_power_stream"):
        for category in ("T1", "T2", "T3"):
            for variant in ((1, 2) if suite == "formal" else (1,)):
                tid = ("F" if suite == "formal" else "D") + f"{len(rows)+1:02d}"
                rows.append(make_task(tid, family, category, variant, rng))
    return rows
