from copy import deepcopy
from math import pi, prod
from pathlib import Path
import tempfile
import unittest
from prototype import engineering as eng
from prototype.cli import read_json
from prototype.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]
CASE = read_json(ROOT/'examples/rapeseed.json')

class DualPowerTests(unittest.TestCase):
    def setUp(self):
        self.case = deepcopy(CASE)

    def solve(self):
        validation = eng.validate_requirements(self.case)
        self.assertEqual(validation['status'], 'ok', validation)
        c = validation['normalized']
        loads = eng.calculate_loads(c)
        return loads, eng.check_constraints(c, eng.match_hst(c, loads)['candidates'])

    def test_willis_kinematics_and_virtual_work(self):
        loads, result = self.solve()
        g, req = self.case['planetary'], self.case['requirements']
        kappa = g['ring_teeth']/g['sun_teeth']
        for s, row in zip(self.case['scenarios'], result['selected']['scenarios']):
            d, t = row['branches']['drive'], row['branches']['steering']
            sun = d['motor_speed_rpm']/g['drive_input_reduction']
            ring = t['motor_speed_rpm']/g['steer_input_reduction']
            for side, sign in [('left', -1), ('right', 1)]:
                carrier = row[f'{side}_speed_m_s']/req['drive_sprocket_effective_radius_m']*60/(2*pi)*g['final_reduction']
                self.assertAlmostEqual((1+kappa)*carrier, sun+sign*kappa*ring)
            output = row['left_force_n']*row['left_speed_m_s'] + row['right_force_n']*row['right_speed_m_s']
            motor_power = sum(x['motor_torque_nm']*x['motor_speed_rpm']*2*pi/60 for x in [d,t])
            self.assertAlmostEqual(output, motor_power*self.case['efficiencies']['mechanical_reduction'])
            self.assertAlmostEqual(row['pump_shaft_power_kw_total'], output/1000/prod(self.case['efficiencies'].values()))

    def test_branch_selection_and_zero_speed_limits(self):
        _, result = self.solve()
        self.assertEqual(len(result['candidates']), 81)
        self.assertEqual(result['selected']['candidate_id'], 'DP35_DM63_SP10_SM32')
        straight, *_, pivot = result['selected']['scenarios']
        self.assertEqual(straight['branches']['steering']['motor_inlet_flow_l_min'], 0)
        self.assertEqual(pivot['branches']['drive']['pump_shaft_power_kw'], 0)
        self.assertAlmostEqual(pivot['left_speed_m_s'], -.3)
        self.assertAlmostEqual(pivot['right_speed_m_s'], .3)

    def test_mirror_turn_preserves_power_and_swaps_tracks(self):
        _, original = self.solve()
        for s in self.case['scenarios']:
            s['yaw_rate_rad_s'] *= -1
        _, mirror = self.solve()
        for a,b in zip(original['selected']['scenarios'], mirror['selected']['scenarios']):
            self.assertAlmostEqual(a['left_speed_m_s'], b['right_speed_m_s'])
            self.assertAlmostEqual(a['left_force_n'], b['right_force_n'])
            self.assertAlmostEqual(a['pump_shaft_power_kw_total'], b['pump_shaft_power_kw_total'])

    def test_inconsistent_gear_geometry_rejected(self):
        for field, value in [('planet_teeth', 31), ('sun_teeth', 30.5), ('final_reduction', 0)]:
            self.case = deepcopy(CASE); self.case['planetary'][field] = value
            self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_missing_turn_load_is_not_invented(self):
        del self.case['scenarios'][3]['turn_resistance_moment_nm']
        self.assertEqual(eng.validate_requirements(self.case)['status'], 'missing_input')

    def test_invalid_turn_regimes(self):
        for index, field, value, status in [(0,'turn_resistance_moment_nm',10,'invalid_input'),
            (4,'resistance_coefficient',.08,'invalid_input'), (3,'slope_deg',5,'model_not_applicable'),
            (3,'speed_m_s',.1,'model_not_applicable')]:
            self.case = deepcopy(CASE); self.case['scenarios'][index][field] = value
            self.assertEqual(eng.validate_requirements(self.case)['status'], status)

    def test_configuration_and_input_family_must_agree(self):
        self.assertEqual(eng.check_configuration('independent_hst', self.case['requirements'], 'dual_power_stream')['status'], 'model_not_applicable')
        self.assertEqual(eng.check_configuration('dual_power_stream', self.case['requirements'], 'dual_power_stream')['status'], 'ok')
        self.case['requirements']['fixed_reduction_ratio'] = 15.6
        self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_adhesion_and_branch_pressure_failures_visible(self):
        _, result = self.solve()
        smallest = result['candidates'][0]
        failures = {f['constraint'] for f in smallest['failures']}
        self.assertIn('drive_pressure', failures)
        self.assertIn('steering_pressure', failures)
        self.case['scenarios'][4]['adhesion_coefficient'] = .1
        _, result = self.solve()
        self.assertIsNone(result['selected'])
        self.assertIn('left_longitudinal_adhesion', {f['constraint'] for f in result['candidates'][0]['failures']})

    def test_specific_branch_candidate_checked(self):
        loads, result = self.solve()
        single = eng.match_hst(self.case, loads, branch_selection=result['selected']['branch_selection'])
        self.assertEqual(len(single['candidates']), 1)
        with self.assertRaises(ValueError):
            eng.match_hst(self.case, loads, 35, 63)
        with self.assertRaises(ValueError):
            eng.match_hst(self.case, loads, branch_selection={'drive_pump_ml_rev': 99})

    def test_full_workflow_reports_and_invalidates_turn_revision(self):
        base = ROOT.parent/'.work/prototype_tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            flow = Workflow.create(Path(directory)/'dual', '双功率流示范')
            def call(name, arguments=None):
                result = flow.execute({'name':name, 'arguments':arguments or {}, 'rationale':'软件测试'}, actor='unit_test')
                self.assertNotEqual(result['status'], 'invalid_input', result)
                return result
            flow.supply(self.case, 'demo_script', '测试')
            call('validate_requirements'); flow.review('G1','accept','demo_script','测试')
            call('check_configuration', {'configuration_id':'dual_power_stream'})
            flow.review('G2','accept','demo_script','测试')
            call('calculate_loads'); call('match_hst'); call('check_constraints')
            flow.review('G3','accept','demo_script','测试'); call('build_report')
            report = (flow.directory/'design_report.md').read_text(encoding='utf-8')
            self.assertIn('直行压差', report)
            self.assertIn('等效转向阻力矩', report)
            flow.revise('pivot','turn_resistance_moment_nm',10000,'demo_script','测试变更')
            self.assertEqual(flow.state['gates'], {})
            self.assertIsNone(flow.state['selected'])

if __name__ == '__main__':
    unittest.main()
