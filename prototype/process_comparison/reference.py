"""Independent arithmetic reference. No imports from prototype engineering modules."""
from itertools import product
from math import pi, sin, cos, radians, copysign


def candidate_specs(case):
    c = case['illustrative_catalog']
    if case.get('design_family') == 'dual_power_stream':
        for dp, dm, sp, sm in product(c['drive']['pump_displacements_ml_rev'], c['drive']['motor_displacements_ml_rev'],
                                    c['steering']['pump_displacements_ml_rev'], c['steering']['motor_displacements_ml_rev']):
            yield f'DP{dp:g}_DM{dm:g}_SP{sp:g}_SM{sm:g}', (dp, dm, sp, sm)
    else:
        for p, m in product(c['pump_displacements_ml_rev'], c['motor_displacements_ml_rev']):
            yield f'P{p:g}_M{m:g}', (p, m)


def reference(case):
    r, e, lim = (case[k] for k in ('requirements', 'efficiencies', 'design_limits'))
    dual = case.get('design_family') == 'dual_power_stream'
    mg = r['working_mass_kg'] * r['gravity_m_s2']
    radius, np = r['drive_sprocket_effective_radius_m'], r['pump_speed_rpm']
    def hydro(torque, speed, pump, motor):
        dp = 2*pi*abs(torque)/(motor*e['motor_mechanical'])
        q = abs(speed)*motor/(1000*e['motor_volumetric'])
        available = pump*np*e['pump_volumetric']/1000
        power = dp*q/(60*e['pump_volumetric']*e['pump_mechanical'])
        return dp, q, available, power
    results = {}
    for cid, spec in candidate_specs(case):
        checks = {}
        for s in case['scenarios']:
            theta = radians(s['slope_deg'])
            force = r['load_factor']*mg*(s['resistance_coefficient']*cos(theta)+sin(theta))
            values = {'pump_speed': (np, lim['pump_speed_limit_rpm'])}
            if not dual:
                speed = s['speed_m_s']*r['fixed_reduction_ratio']*60/(2*pi*radius)
                torque = force*radius/(2*r['fixed_reduction_ratio']*e['mechanical_reduction'])
                dp, q, avail, power = hydro(torque, speed, *spec)
                values.update(adhesion=(force, s['adhesion_coefficient']*mg*cos(theta)),
                              pressure=(dp, lim['working_pressure_limit_mpa']),
                              motor_speed=(speed, lim['motor_speed_limit_rpm']), flow=(q, avail),
                              travel_power=(2*power, r['travel_shaft_power_budget_kw']))
            else:
                g = case['planetary']
                k = g['ring_teeth']/g['sun_teeth']
                a = 1/((1+k)*g['drive_input_reduction']*g['final_reduction'])
                b = k/((1+k)*g['steer_input_reduction']*g['final_reduction'])
                w, yaw = r['track_center_distance_m'], s['yaw_rate_rad_s']
                moment = copysign(r['load_factor']*s['turn_resistance_moment_nm'], yaw) if yaw else 0
                cap = s['adhesion_coefficient']*mg*cos(theta)/2
                values.update(left_longitudinal_adhesion=(abs(force/2-moment/w), cap),
                              right_longitudinal_adhesion=(abs(force/2+moment/w), cap))
                nd = s['speed_m_s']*60/(2*pi*radius*a)
                ns = w*yaw*60/(4*pi*radius*b)
                td = a*radius*force/e['mechanical_reduction']
                ts = 2*b*radius*moment/(w*e['mechanical_reduction'])
                total = 0
                for branch, t, n, pump, motor in [('drive', td, nd, spec[0], spec[1]), ('steering', ts, ns, spec[2], spec[3])]:
                    dp, q, avail, power = hydro(t, n, pump, motor)
                    total += power
                    values[branch+'_pressure'] = (dp, lim['working_pressure_limit_mpa'])
                    values[branch+'_motor_speed'] = (abs(n), lim['motor_speed_limit_rpm'])
                    values[branch+'_flow'] = (q, avail)
                values['travel_power'] = (total, r['travel_shaft_power_budget_kw'])
            checks[s['id']] = {k: {'required': x, 'available': y, 'passed': x <= y+1e-9} for k, (x,y) in values.items()}
        results[cid] = {'spec': spec, 'checks': checks, 'feasible': all(c['passed'] for row in checks.values() for c in row.values())}
    feasible = sorted((cid for cid in results if results[cid]['feasible']), key=lambda c: results[c]['spec'])
    return {'candidates': results, 'feasible_ids': feasible, 'ranked_first': feasible[0] if feasible else None}
