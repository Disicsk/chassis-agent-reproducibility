"""Offline task construction from physical criteria, never from model outcomes.

All instantiated task text, answer tables and reference solutions are private files.
"""
import random
from copy import deepcopy
from math import isclose
from pathlib import Path
from .. import engineering as eng
from .reference import reference
from .util import flatten, get, put, remove, digest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def base(family):
    from .util import read
    x = read(EXAMPLES / ("rice_wheat.json" if family == "independent_hst" else "rapeseed.json"))
    x.pop("parameter_provenance", None)
    x["design_family"] = family
    x["title"] = "合成工程设计任务"
    return x


def path_for(case, sid, field):
    return f"scenarios.{next(i for i, s in enumerate(case['scenarios']) if s['id'] == sid)}.{field}"


def vary(family, rng):
    x = base(family)
    r = x["requirements"]
    for key, lo, hi, digits in [
        ("working_mass_kg", .83, 1.17, 0), ("drive_sprocket_effective_radius_m", .91, 1.09, 3),
        ("pump_speed_rpm", .92, 1.06, 0), ("load_factor", .97, 1.05, 3),
        ("track_center_distance_m", .94, 1.06, 3)]:
        r[key] = round(r[key] * rng.uniform(lo, hi), digits)
    if family == "independent_hst":
        r["fixed_reduction_ratio"] = round(r["fixed_reduction_ratio"] * rng.uniform(.88, 1.12), 2)
        parts = [x["illustrative_catalog"]]
    else:
        for key in ["drive_input_reduction", "steer_input_reduction", "final_reduction"]:
            x["planetary"][key] = round(x["planetary"][key] * rng.uniform(.92, 1.08), 3)
        parts = [x["illustrative_catalog"][b] for b in ["drive", "steering"]]
    for part in parts:
        for key in ["pump_displacements_ml_rev", "motor_displacements_ml_rev"]:
            factor = rng.uniform(.91, 1.13)
            part[key] = sorted({max(1, int(round(v * factor))) for v in part[key]})
    for s in x["scenarios"]:
        if s["speed_m_s"]:
            s["speed_m_s"] = round(s["speed_m_s"] * rng.uniform(.87, 1.13), 3)
        if s["slope_deg"]:
            s["slope_deg"] = round(s["slope_deg"] * rng.uniform(.80, 1.05), 2)
        if s["resistance_coefficient"]:
            s["resistance_coefficient"] = round(s["resistance_coefficient"] * rng.uniform(.88, 1.10), 4)
        if s.get("yaw_rate_rad_s"):
            s["yaw_rate_rad_s"] = round(s["yaw_rate_rad_s"] * rng.uniform(.88, 1.08), 4)
            s["turn_resistance_moment_nm"] = round(s["turn_resistance_moment_nm"] * rng.uniform(.85, 1.10), 1)
    r["travel_shaft_power_budget_kw"] = round(r["travel_shaft_power_budget_kw"] * rng.uniform(.96, 1.15), 2)
    r["engine_rated_power_kw"] = max(r["engine_rated_power_kw"], round(r["travel_shaft_power_budget_kw"] * 1.3, 2))
    return x


def crosscheck(case):
    val = eng.validate_requirements(case)
    if val["status"] != "ok":
        return None
    expected = reference(case)
    actual = eng.check_constraints(case, eng.match_hst(case, eng.calculate_loads(case))["candidates"])
    for candidate in actual["candidates"]:
        other = expected["candidates"][candidate["candidate_id"]]
        assert candidate["passes_modeled_checks"] == other["feasible"]
        for row in candidate["scenarios"]:
            for key, item in row["checks"].items():
                check = other["checks"][row["scenario_id"]][key]
                assert item["passed"] == check["passed"]
                assert all(isclose(item[k], check[k], rel_tol=1e-10, abs_tol=1e-8)
                           for k in ["required", "available"])
    return expected


def make_task(tid, family, category, variant, rng):
    prior = None
    for attempt in range(400):
        full = vary(family, rng)
        full["case_id"] = tid
        ref = crosscheck(full)
        if not ref or len(ref["feasible_ids"]) < 2 or len(ref["feasible_ids"]) == len(ref["candidates"]):
            continue
        if category != "S3":
            break
        original = deepcopy(full)
        old_id = ref["ranked_first"]
        speed = path_for(full, "transfer_flat", "speed_m_s")
        if variant == 1:
            put(full, speed, round(get(full, speed) * .88, 3))
            full["requirements"]["travel_shaft_power_budget_kw"] = round(
                full["requirements"]["travel_shaft_power_budget_kw"] * 1.04, 2)
            new_ref = crosscheck(full)
            if not new_ref or old_id not in new_ref["feasible_ids"]:
                continue
        else:
            found = False
            for multiplier in [1.12, 1.20, 1.30, 1.40, 1.55, 1.70, 1.85]:
                full = deepcopy(original)
                put(full, speed, round(get(original, speed) * multiplier, 3))
                full["requirements"]["travel_shaft_power_budget_kw"] = round(
                    min(original["requirements"]["engine_rated_power_kw"] * .95,
                        original["requirements"]["travel_shaft_power_budget_kw"] * 1.12), 2)
                new_ref = crosscheck(full)
                if new_ref and new_ref["feasible_ids"] and old_id not in new_ref["feasible_ids"]:
                    found = True
                    break
            if not found:
                continue
        prior = {"case": original, "candidate_id": old_id,
                 "checks": ref["candidates"][old_id]["checks"],
                 "source": "common supplied starting design; verified before the task; not the revised answer"}
        ref = new_ref
        break
    else:
        raise ValueError("Unable to construct task under predeclared physical criteria")
    interface = ("左右履带分别由各自的变量泵、马达与减速器驱动，保留既有固定总减速比。"
                 if family == "independent_hst" else
                 "直行马达同向输入两太阳轮，转向马达反向输入两齿圈，行星架经末级减速驱动左右履带。")
    request = "为履带式联合收获机完成底盘初步设计。" + interface + "元件仅能从所给目录选择，所有已给定效率与限值保持不变。"
    full_flat = flatten(full)
    initial = deepcopy(prior["case"] if prior else full)
    answers, source_values, ambiguous = {}, {}, []
    if category == "S3":
        changes = {p: v for p, v in full_flat.items() if get(prior["case"], p) != v}
        request += "现提供已有合格方案，请依据以下新要求调整或确认方案；不要沿用已改变的旧输入。"
        request += "新要求：" + "；".join(f"{p}={v}" for p, v in changes.items()) + "。"
        source_values = changes
    else:
        remove(initial, "design_family")
        natural_paths = ["requirements.working_mass_kg", "requirements.pump_speed_rpm",
                         path_for(full, "work_flat", "speed_m_s"),
                         path_for(full, "transfer_flat", "speed_m_s"), path_for(full, "climb", "slope_deg")]
        if category == "S2":
            if variant == 1:
                answer_paths = ["requirements.drive_sprocket_effective_radius_m",
                                "requirements.travel_shaft_power_budget_kw"]
                request += "主动轮有效半径和可分配给行走的泵轴功率尚未列出，需要向工程师确认。"
            else:
                answer_paths = [path_for(full, "transfer_flat", "speed_m_s"),
                                "requirements.travel_shaft_power_budget_kw"]
                request += "转场速度与行走功率预算各有两版描述，均未标定优先级，需要确认后再采用。"
                for p in answer_paths:
                    v = get(full, p)
                    request += f"{p}的两项记录为{v}与{round(v * 1.16, 3)}，记录顺序不代表有效性。"
                ambiguous = answer_paths
            answers = {p: get(full, p) for p in answer_paths}
        else:
            answer_paths = []
        for p in natural_paths:
            if p not in answer_paths:
                source_values[p] = get(full, p)
            remove(initial, p)
        for p in answer_paths:
            remove(initial, p)
        request += "文字中已给定的现行参数：" + "；".join(f"{p}={v}" for p, v in source_values.items()) + "。其余工程输入见数据附件。"
    # Fact authorization is external: it checks/returns published facts and explicit answers,
    # never chooses a candidate and never supplies a target case to a controller.
    authorized = flatten(initial)
    authorized.update(source_values)
    authorized.pop("design_family", None)
    for p in answers:
        authorized.pop(p, None)
    requirements = [p for p in full_flat if p.startswith(("requirements.", "scenarios.", "planetary.", "design_limits."))]
    requirements = [p for p in requirements if not p.endswith((".label", ".id", ".configuration_preference",
                        ".crop", ".header_width_m", ".feed_capacity_reference_kg_s",
                        ".track_contact_length_m", ".track_width_m"))]
    return {"id": tid, "family": family, "category": category, "variant": variant,
            "public": {"request": request, "case": initial, "starting_design": prior,
                       "interface_description": interface},
            "external_policy": {"initial_authorized": authorized, "answer_table": answers,
                                "ambiguous_fields": ambiguous},
            "private": {"target": full, "reference": ref, "requirement_paths": ["design_family", *requirements],
                        "prior_remains_feasible": prior["candidate_id"] in ref["feasible_ids"] if prior else None,
                        "construction_attempt": attempt + 1, "target_digest": digest(full)}}


def build_tasks(suite):
    rng = random.Random(2026102081 if suite == "formal" else 2026102043)
    tasks = []
    for family in ["independent_hst", "dual_power_stream"]:
        for category in ["S1", "S2", "S3"]:
            for variant in ([1, 2] if suite == "formal" else [2 if family == "dual_power_stream" else 1]):
                tid = ("F" if suite == "formal" else "D") + f"{len(tasks) + 1:02d}"
                tasks.append(make_task(tid, family, category, variant, rng))
    assert len({x["private"]["target_digest"] for x in tasks}) == len(tasks)
    return tasks
