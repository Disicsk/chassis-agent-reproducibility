"""Two HST inputs, common suns / opposing rings, carriers to final reductions.

Kinematics use Willis' equation; torque uses virtual work. The declared yaw
resistance is an input, not a terramechanics prediction. A lumped mechanical
efficiency approximates losses; internal circulating-power losses are not solved.
"""
from copy import deepcopy
from itertools import product
from math import cos, sin, radians, pi, copysign
from .engineering import quantity

MODEL_ID = 'dual_power_stream_planetary_quasistatic_v1'
NOT_EVALUATED = ['等效转向阻力矩的实测辨识与土壤机理预测', '横向附着/纵横向耦合与滑移',
    '动态、制动、热平衡、结构强度与寿命', '内部循环功率损失与零速保压泄漏',
    '具体齿轮布置、安装干涉及厂家产品可采购性', '喂入量与作业速度耦合']

def calculate_loads(case):
    req, eff, gear = case['requirements'], case['efficiencies'], case['planetary']
    kappa = gear['ring_teeth']/gear['sun_teeth']
    a = 1/((1+kappa)*gear['drive_input_reduction']*gear['final_reduction'])
    b = kappa/((1+kappa)*gear['steer_input_reduction']*gear['final_reduction'])
    radius, width = req['drive_sprocket_effective_radius_m'], req['track_center_distance_m']
    mg = req['working_mass_kg']*req['gravity_m_s2']
    rows = []
    for s in case['scenarios']:
        v, yaw = s['speed_m_s'], s['yaw_rate_rad_s']
        theta = radians(s['slope_deg'])
        force = req['load_factor']*mg*(s['resistance_coefficient']*cos(theta)+sin(theta))
        moment = copysign(req['load_factor']*s['turn_resistance_moment_nm'], yaw) if yaw else 0
        left_v, right_v = v-width*yaw/2, v+width*yaw/2
        left_f, right_f = force/2-moment/width, force/2+moment/width
        nd, ns = v/radius/a*60/(2*pi), width*yaw/(2*radius*b)*60/(2*pi)
        td, ts = a*radius*force/eff['mechanical_reduction'], b*radius*2*moment/width/eff['mechanical_reduction']
        rows.append({'scenario_id': s['id'], 'label': s.get('label', s['id']),
            'design_force_n': force, 'design_yaw_moment_nm': moment,
            'left_speed_m_s': left_v, 'right_speed_m_s': right_v,
            'left_force_n': left_f, 'right_force_n': right_f,
            'adhesion_capacity_n_each': s['adhesion_coefficient']*mg*cos(theta)/2,
            'ground_power_kw': (force*v+moment*yaw)/1000,
            'branches': {'drive': {'motor_speed_rpm': nd, 'motor_torque_nm': td},
                         'steering': {'motor_speed_rpm': ns, 'motor_torque_nm': ts}}})
    return {'status': 'ok', 'model_id': MODEL_ID, 'coefficients': {'A': a, 'B': b, 'ring_sun_ratio': kappa}, 'scenarios': rows}

def match_hst(case, loads, selection=None):
    req, eff, catalog = case['requirements'], case['efficiencies'], case['illustrative_catalog']
    keys = ['drive_pump_ml_rev', 'drive_motor_ml_rev', 'steering_pump_ml_rev', 'steering_motor_ml_rev']
    if selection is not None and (not isinstance(selection, dict) or set(selection) != set(keys)):
        raise ValueError('branch_selection 必须完整包含两支路的四个排量字段')
    choices = []
    for branch in ['drive', 'steering']:
        for component in ['pump', 'motor']:
            allowed = catalog[branch][f'{component}_displacements_ml_rev']
            values = allowed if selection is None else [quantity(selection[f'{branch}_{component}_ml_rev'], 'mL/rev')]
            if any(value not in allowed for value in values):
                raise ValueError('指定排量不在已确认的支路候选表内')
            choices.append(values)
    candidates = []
    for dp, dm, sp, sm in product(*choices):
        specs = {'drive': {'pump_ml_rev': dp, 'motor_ml_rev': dm}, 'steering': {'pump_ml_rev': sp, 'motor_ml_rev': sm}}
        rows = deepcopy(loads['scenarios'])
        for row in rows:
            total = 0
            for branch, spec in specs.items():
                r = row['branches'][branch]
                # Reversing both speed and torque remains a motoring input.
                if r['motor_speed_rpm']*r['motor_torque_nm'] < -1e-9:
                    raise ValueError('当前模型不支持 HST 支路再生/回馈')
                pressure = 2*pi*abs(r['motor_torque_nm'])/(spec['motor_ml_rev']*eff['motor_mechanical'])
                flow = abs(r['motor_speed_rpm'])*spec['motor_ml_rev']/(1000*eff['motor_volumetric'])
                power = pressure*flow/(60*eff['pump_volumetric']*eff['pump_mechanical'])
                r.update({'pressure_mpa': pressure, 'motor_inlet_flow_l_min': flow,
                    'required_pump_displacement_ml_rev': flow*1000/(req['pump_speed_rpm']*eff['pump_volumetric']),
                    'available_pump_flow_l_min': spec['pump_ml_rev']*req['pump_speed_rpm']*eff['pump_volumetric']/1000,
                    'pump_shaft_power_kw': power})
                total += power
            row['pump_shaft_power_kw_total'] = total
        candidates.append({'candidate_id': f'DP{dp:g}_DM{dm:g}_SP{sp:g}_SM{sm:g}', 'branch_selection': dict(zip(keys, [dp,dm,sp,sm])),
            'branch_specs': specs, 'scenarios': rows})
    return {'status': 'ok', 'candidates': candidates}

def check_constraints(case, candidates):
    req, limits = case['requirements'], case['design_limits']
    checked = deepcopy(candidates)
    for candidate in checked:
        failures = []
        for row in candidate['scenarios']:
            values = {'left_longitudinal_adhesion': (abs(row['left_force_n']), row['adhesion_capacity_n_each'], 'N'),
                'right_longitudinal_adhesion': (abs(row['right_force_n']), row['adhesion_capacity_n_each'], 'N'),
                'pump_speed': (req['pump_speed_rpm'], limits['pump_speed_limit_rpm'], 'r/min'),
                'travel_power': (row['pump_shaft_power_kw_total'], req['travel_shaft_power_budget_kw'], 'kW')}
            for branch, r in row['branches'].items():
                values.update({f'{branch}_pressure': (r['pressure_mpa'], limits['working_pressure_limit_mpa'], 'MPa'),
                    f'{branch}_motor_speed': (abs(r['motor_speed_rpm']), limits['motor_speed_limit_rpm'], 'r/min'),
                    f'{branch}_flow': (r['motor_inlet_flow_l_min'], r['available_pump_flow_l_min'], 'L/min')})
            row['checks'] = {name: {'required': x, 'available': y, 'unit': unit, 'passed': x <= y+1e-9} for name, (x,y,unit) in values.items()}
            failures += [{'scenario_id': row['scenario_id'], 'constraint': name, **item} for name, item in row['checks'].items() if not item['passed']]
        candidate['failures'] = failures
        candidate['passes_modeled_checks'] = not failures
    feasible = [c for c in checked if c['passes_modeled_checks']]
    feasible.sort(key=lambda c: tuple(c['branch_selection'][k] for k in ['drive_pump_ml_rev', 'drive_motor_ml_rev', 'steering_pump_ml_rev', 'steering_motor_ml_rev']))
    return {'status': 'feasible_within_scope' if feasible else 'constraint_failed', 'candidates': checked,
        'selected': feasible[0] if feasible else None,
        'ranking_rule': '满足已设约束后，按直行泵、直行马达、转向泵、转向马达排量升序选择；非综合最优',
        'not_evaluated': NOT_EVALUATED}
