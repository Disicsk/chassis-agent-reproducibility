"""Deterministic, unit-aware calculations for symmetric steady forward travel."""
from copy import deepcopy
from math import cos, sin, pi, radians, isfinite

MODEL_ID = "independent_hst_steady_straight_v1"
CONFIGURATIONS = {
    "independent_hst": {
        "label": "左右独立双 HST",
        "model_id": MODEL_ID,
        "connections": ["发动机→左变量泵→左马达→左减速器→左履带", "发动机→右变量泵→右马达→右减速器→右履带"],
        "capabilities": ["continuous_radius", "counterrotation_pivot"],
        "note": "两侧独立调速和换向；原地转向仅确认结构/运动学能力，未评价土壤转向负载。",
    },
    "dual_power_stream": {
        "label": "直行/转向双功率流合成",
        "model_id": "dual_power_stream_planetary_quasistatic_v1",
        "connections": ["直行输入与转向输入→行星合成机构→左右履带"],
        "capabilities": ["continuous_radius", "counterrotation_pivot"],
        "note": "直行马达同向驱动两太阳轮，转向马达反向驱动两齿圈，行星架经末级减速输出；用给定等效转向阻力矩作准静态演示。",
    },
    "single_hst_brake": {
        "label": "单 HST 配单边制动转向",
        "model_id": None,
        "connections": ["HST→机械传动与单边制动→左右履带"],
        "capabilities": [],
        "note": "仅单边制动不能据此认定支持两侧反向原地转向。",
    },
}
NOT_EVALUATED = ["土壤转向阻力及转向压力/功率", "滑移与动态过程", "制动、热平衡及结构寿命", "安装干涉", "喂入量与作业速度耦合"]
REQ = {
    "working_mass_kg": ("kg", 0, None),
    "travel_shaft_power_budget_kw": ("kW", 0, None),
    "drive_sprocket_effective_radius_m": ("m", 0, None),
    "fixed_reduction_ratio": ("1", 0, None),
    "pump_speed_rpm": ("r/min", 0, None),
    "load_factor": ("1", 0, None),
    "gravity_m_s2": ("m/s2", 0, None),
}
OPTIONAL_REQ = {
    "engine_rated_power_kw": ("kW", 0, None),
    "header_width_m": ("m", 0, None),
    "feed_capacity_reference_kg_s": ("kg/s", 0, None),
    "track_center_distance_m": ("m", 0, None),
    "track_contact_length_m": ("m", 0, None),
    "track_width_m": ("m", 0, None),
}
EFF = {k: ("1", 0, 1) for k in ["pump_volumetric", "pump_mechanical", "motor_volumetric", "motor_mechanical", "mechanical_reduction"]}
LIMITS = {"working_pressure_limit_mpa": ("MPa", 0, None), "motor_speed_limit_rpm": ("r/min", 0, None), "pump_speed_limit_rpm": ("r/min", 0, None)}
SCENARIO = {"speed_m_s": ("m/s", 0, None), "slope_deg": ("deg", 0, 60), "resistance_coefficient": ("1", 0, 1), "adhesion_coefficient": ("1", 0, 2), "acceleration_m_s2": ("m/s2", 0, None)}
TURN_SCENARIO = {"yaw_rate_rad_s": ("rad/s", -10, 10), "turn_resistance_moment_nm": ("N*m", 0, None)}
PLANETARY = {k: ("1", 0, None) for k in ["sun_teeth", "ring_teeth", "planet_teeth", "drive_input_reduction", "steer_input_reduction", "final_reduction"]}
CONVERSIONS = {
    "kg": {"kg": 1, "t": 1000}, "m": {"m": 1, "mm": .001, "cm": .01},
    "m/s": {"m/s": 1, "km/h": 1/3.6}, "MPa": {"MPa": 1, "bar": .1, "Pa": 1e-6},
    "kW": {"kW": 1, "W": .001}, "r/min": {"r/min": 1, "rpm": 1},
    "mL/rev": {"mL/rev": 1, "mL/r": 1, "cc/rev": 1},
    "deg": {"deg": 1, "°": 1}, "1": {"1": 1, "": 1},
    "m/s2": {"m/s2": 1, "m/s²": 1}, "kg/s": {"kg/s": 1},
    "rad/s": {"rad/s": 1}, "N*m": {"N*m": 1, "N·m": 1, "kN*m": 1000},
}

def quantity(raw, unit):
    value = raw
    if isinstance(raw, dict):
        value = raw.get("value")
        supplied = raw.get("unit", unit)
        if supplied not in CONVERSIONS[unit]:
            raise ValueError(f"不能将 {supplied} 转为 {unit}")
        factor = CONVERSIONS[unit][supplied]
    else:
        factor = 1
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("必须为有限数值")
    converted = value * factor
    if not isfinite(converted):
        raise ValueError('单位换算后不是有限数值')
    return converted

def validate_requirements(case):
    c = deepcopy(case)
    missing, errors, unsupported = [], [], []
    if not isinstance(c, dict):
        return {"status": "invalid_input", "errors": ["输入必须为对象"], "missing": []}
    family = c.get("design_family", "independent_hst")
    if family not in ["independent_hst", "dual_power_stream"]:
        unsupported.append("design_family: 未实现的构型参数模型")
    dual = family == "dual_power_stream"

    def fields(obj, definitions, prefix, required=True, zero_allowed=False):
        if not isinstance(obj, dict):
            errors.append(f"{prefix} 必须为对象")
            return
        for key, (unit, low, high) in definitions.items():
            path = f"{prefix}.{key}"
            if obj.get(key) is None:
                if required:
                    missing.append(path)
                continue
            try:
                value = quantity(obj[key], unit)
                if value < low or (not zero_allowed and value == low) or (high is not None and value > high):
                    raise ValueError(f"超出允许范围（下界 {low}，上界 {high}）")
                obj[key] = value
            except (ValueError, TypeError) as exc:
                errors.append(f"{path}: {exc}")

    required = {k: v for k, v in REQ.items() if not (dual and k == "fixed_reduction_ratio")}
    for name, defs in [("requirements", required), ("efficiencies", EFF), ("design_limits", LIMITS)]:
        obj = c.setdefault(name, {})
        fields(obj, defs, name)
    req = c.get("requirements")
    if isinstance(req, dict):
        fields(req, OPTIONAL_REQ, "requirements", required=False)
        turns = req.get("required_turn_modes")
        if turns is None:
            missing.append("requirements.required_turn_modes")
        elif not isinstance(turns, list) or any(t not in ["continuous_radius", "counterrotation_pivot", "ordinary_turn"] for t in turns):
            errors.append("requirements.required_turn_modes: 转向需求无效")
        if isinstance(turns, list) and "counterrotation_pivot" in turns and req.get("track_center_distance_m") is None:
            missing.append("requirements.track_center_distance_m")
        if dual and req.get("track_center_distance_m") is None and "requirements.track_center_distance_m" not in missing:
            missing.append("requirements.track_center_distance_m")
        if dual and "fixed_reduction_ratio" in req:
            errors.append("双功率流速比由 planetary 定义，请移除 fixed_reduction_ratio，避免两套定义冲突")
    if dual:
        gear = c.setdefault("planetary", {})
        fields(gear, PLANETARY, "planetary")
        if isinstance(gear, dict) and not errors and all(k in gear and gear[k] is not None for k in PLANETARY):
            if any(gear[k] != int(gear[k]) for k in ["sun_teeth", "ring_teeth", "planet_teeth"]) or gear["ring_teeth"] != gear["sun_teeth"] + 2*gear["planet_teeth"]:
                errors.append("planetary: 齿数须为正整数，且 Nr = Ns + 2Np")
    scenarios = c.get("scenarios")
    if scenarios is None or scenarios == []:
        missing.append("scenarios")
    elif not isinstance(scenarios, list) or len(scenarios) > 30:
        errors.append("scenarios 必须为不超过 30 项的列表")
    else:
        ids = set()
        for index, s in enumerate(scenarios):
            if not isinstance(s, dict):
                errors.append(f"scenarios[{index}] 必须为对象")
                continue
            sid = s.get("id")
            if not isinstance(sid, str) or not sid or sid in ids:
                errors.append(f"scenarios[{index}].id 缺失或重复")
            else:
                ids.add(sid)
            # Reject unsupported operating regimes explicitly, not by signed power checks.
            for key in ["speed_m_s", "slope_deg", "acceleration_m_s2"]:
                raw = s.get(key)
                if raw is not None:
                    try:
                        if quantity(raw, SCENARIO[key][0]) < 0:
                            unsupported.append(f"{sid}.{key}: 本模型不支持倒行、下坡制动或减速回馈")
                    except (ValueError, TypeError):
                        pass
            fields(s, SCENARIO, f"scenarios[{index}]", zero_allowed=True)
            if dual:
                fields(s, TURN_SCENARIO, f"scenarios[{index}]", zero_allowed=True)
                if not errors and all(s.get(k) is not None for k in [*SCENARIO, *TURN_SCENARIO]):
                    yaw, moment = s['yaw_rate_rad_s'], s['turn_resistance_moment_nm']
                    if (yaw == 0) != (moment == 0):
                        errors.append(f"{sid}: 直行的转向阻力矩须为 0；转向须给出正的等效阻力矩")
                    if yaw != 0 and s['slope_deg'] != 0:
                        unsupported.append(f"{sid}: 当前转向算例只支持平地")
                    if yaw != 0 and s['speed_m_s'] == 0 and s['resistance_coefficient'] != 0:
                        errors.append(f"{sid}: 原地转向的滚阻已计入等效阻力矩，resistance_coefficient 须为 0")
                    width = req.get('track_center_distance_m') if isinstance(req, dict) else None
                    if isinstance(width, (int, float)) and 0 < s['speed_m_s'] < abs(yaw)*width/2:
                        unsupported.append(f"{sid}: 暂不支持移动中内侧履带反转，使用同向缓弯或纯原地转向")
            elif any(s.get(k, 0) != 0 for k in TURN_SCENARIO):
                unsupported.append(f"{sid}: 独立 HST 工具仅覆盖直行，不得忽略转向负载输入")
            for k in ["speed_m_s", "slope_deg", "acceleration_m_s2"]:
                if isinstance(s.get(k), (int, float)) and s[k] != 0 and k == "acceleration_m_s2":
                    unsupported.append(f"{sid}: 当前仅实现稳态直行，加速度须为 0")
    catalog = c.get("illustrative_catalog")
    if catalog is None:
        missing.append("illustrative_catalog")
    elif not isinstance(catalog, dict):
        errors.append("illustrative_catalog 必须为对象")
    else:
        branches = ['drive', 'steering'] if dual else [None]
        combination_count = 1
        for branch in branches:
            part = catalog.get(branch) if branch else catalog
            prefix = f'illustrative_catalog.{branch}' if branch else 'illustrative_catalog'
            if part is None:
                missing.append(prefix)
                continue
            if not isinstance(part, dict):
                errors.append(f'{prefix} 必须为对象')
                continue
            for key in ["pump_displacements_ml_rev", "motor_displacements_ml_rev"]:
                values = part.get(key)
                if values is None or values == []:
                    missing.append(f"{prefix}.{key}")
                elif not isinstance(values, list) or len(values) > 50:
                    errors.append(f"{prefix}.{key} 必须为不超过 50 项的列表")
                else:
                    try:
                        values = [quantity(x, "mL/rev") for x in values]
                        if min(values) <= 0:
                            raise ValueError("排量必须为正数")
                        part[key] = sorted(set(values))
                        combination_count *= len(part[key])
                    except (ValueError, TypeError) as exc:
                        errors.append(f"{prefix}.{key}: {exc}")
            counts = ['pump_count', 'motor_count'] if dual else ['pump_count_for_independent_drive', 'motor_count_for_independent_drive']
            for key in counts:
                expected = 1 if dual else 2
                if part.get(key) is None:
                    missing.append(f"{prefix}.{key}")
                elif part[key] != expected or isinstance(part[key], bool):
                    unsupported.append(f"{prefix}.{key}: 本模型要求 {expected} 台")
        if combination_count > 2500:
            errors.append('候选组合超过 2500，请缩小候选表')
    if not errors and isinstance(req, dict):
        engine, budget = req.get("engine_rated_power_kw"), req.get("travel_shaft_power_budget_kw")
        if engine is not None and budget is not None and budget > engine:
            errors.append("行走泵轴功率预算超过发动机额定功率")
    status = "model_not_applicable" if unsupported else "invalid_input" if errors else "missing_input" if missing else "ok"
    return {"status": status, "normalized": c, "missing": missing, "errors": errors, "unsupported": unsupported}

def check_configuration(configuration_id, requirements, design_family='independent_hst'):
    cfg = CONFIGURATIONS.get(configuration_id)
    if cfg is None:
        return {"status": "invalid_input", "reason": "未知构型", "available": list(CONFIGURATIONS)}
    cfg = deepcopy(cfg)
    wanted = set(requirements.get("required_turn_modes", [])) - {"ordinary_turn"}
    if not wanted.issubset(cfg["capabilities"]):
        return {"status": "constraint_failed", "reason": "构型不能据现有规则满足所需转向能力", "configuration": cfg}
    if cfg["model_id"] is None:
        return {"status": "model_not_applicable", "reason": "构型可讨论，但本版尚未实现其参数模型", "configuration": cfg}
    if configuration_id != design_family:
        return {"status": "model_not_applicable", "reason": "输入参数模型与所选构型不一致，须补齐该构型输入并重新确认 G1", "configuration": cfg}
    return {"status": "ok", "configuration_id": configuration_id, "configuration": cfg}

def calculate_loads(case):
    if case.get('design_family') == 'dual_power_stream':
        from .dual_power import calculate_loads as dual_loads
        return dual_loads(case)
    req, eff = case["requirements"], case["efficiencies"]
    rows = []
    for s in case["scenarios"]:
        theta = radians(s["slope_deg"])
        mg = req["working_mass_kg"] * req["gravity_m_s2"]
        force = req["load_factor"] * mg * (s["resistance_coefficient"] * cos(theta) + sin(theta))
        rows.append({"scenario_id": s["id"], "label": s.get("label", s["id"]),
            "design_force_n": force,
            "adhesion_capacity_n": s["adhesion_coefficient"] * mg * cos(theta),
            "motor_torque_nm_each": force * req["drive_sprocket_effective_radius_m"] / (2 * req["fixed_reduction_ratio"] * eff["mechanical_reduction"]),
            "motor_speed_rpm": 60 * req["fixed_reduction_ratio"] * s["speed_m_s"] / (2*pi*req["drive_sprocket_effective_radius_m"])})
    return {"status": "ok", "model_id": MODEL_ID, "scenarios": rows}

def match_hst(case, loads, pump=None, motor=None, branch_selection=None):
    if case.get('design_family') == 'dual_power_stream':
        if pump is not None or motor is not None:
            raise ValueError('双功率流须使用 branch_selection 分别指定两支路')
        from .dual_power import match_hst as dual_match
        return dual_match(case, loads, branch_selection)
    if branch_selection is not None:
        raise ValueError('独立 HST 不接受双功率流支路参数')
    req, eff, catalog = case["requirements"], case["efficiencies"], case["illustrative_catalog"]
    pumps = catalog["pump_displacements_ml_rev"] if pump is None else [quantity(pump, "mL/rev")]
    motors = catalog["motor_displacements_ml_rev"] if motor is None else [quantity(motor, "mL/rev")]
    if any(p not in catalog["pump_displacements_ml_rev"] for p in pumps) or any(m not in catalog["motor_displacements_ml_rev"] for m in motors):
        raise ValueError("指定排量不在已确认的候选表内")
    candidates = []
    for p in pumps:
        for m in motors:
            rows = []
            for load in loads["scenarios"]:
                row = deepcopy(load)
                pressure = 2*pi*row["motor_torque_nm_each"]/(m*eff["motor_mechanical"])
                flow = row["motor_speed_rpm"]*m/(1000*eff["motor_volumetric"])
                row.update({"pressure_mpa": pressure, "motor_inlet_flow_l_min_each": flow,
                    "required_pump_displacement_ml_rev_each": 1000*flow/(req["pump_speed_rpm"]*eff["pump_volumetric"]),
                    "available_pump_flow_l_min_each": p*req["pump_speed_rpm"]*eff["pump_volumetric"]/1000,
                    "pump_shaft_power_kw_total": 2*pressure*flow/(60*eff["pump_volumetric"]*eff["pump_mechanical"])})
                rows.append(row)
            candidates.append({"candidate_id": f"P{p:g}_M{m:g}", "pump_displacement_ml_rev_each": p, "motor_displacement_ml_rev_each": m, "scenarios": rows})
    return {"status": "ok", "candidates": candidates}

def check_constraints(case, candidates):
    if case.get('design_family') == 'dual_power_stream':
        from .dual_power import check_constraints as dual_check
        return dual_check(case, candidates)
    req, limits = case["requirements"], case["design_limits"]
    checked = deepcopy(candidates)
    for candidate in checked:
        failures = []
        for row in candidate["scenarios"]:
            values = {
                "adhesion": (row["design_force_n"], row["adhesion_capacity_n"], "N"),
                "pressure": (row["pressure_mpa"], limits["working_pressure_limit_mpa"], "MPa"),
                "motor_speed": (row["motor_speed_rpm"], limits["motor_speed_limit_rpm"], "r/min"),
                "pump_speed": (req["pump_speed_rpm"], limits["pump_speed_limit_rpm"], "r/min"),
                "flow": (row["motor_inlet_flow_l_min_each"], row["available_pump_flow_l_min_each"], "L/min"),
                "travel_power": (row["pump_shaft_power_kw_total"], req["travel_shaft_power_budget_kw"], "kW"),
            }
            row["checks"] = {name: {"required": a, "available": b, "unit": unit, "passed": a <= b + 1e-9} for name, (a, b, unit) in values.items()}
            failures += [{"scenario_id": row["scenario_id"], "constraint": name, **item} for name, item in row["checks"].items() if not item["passed"]]
        candidate["failures"] = failures
        candidate["passes_modeled_checks"] = not failures
    feasible = [c for c in checked if c["passes_modeled_checks"]]
    feasible.sort(key=lambda c: (c["pump_displacement_ml_rev_each"], c["motor_displacement_ml_rev_each"]))
    return {"status": "feasible_within_scope" if feasible else "constraint_failed", "candidates": checked,
        "selected": feasible[0] if feasible else None,
        "ranking_rule": "先满足已设约束，再依次选择较小泵排量、较小马达排量；非综合最优",
        "not_evaluated": NOT_EVALUATED}
