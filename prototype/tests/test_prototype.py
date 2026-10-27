from copy import deepcopy
import json
from math import prod
import os
from pathlib import Path
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch

from prototype import engineering as eng
from prototype.api import run_api, post_response
from prototype.cli import read_json, run_demo, main
from prototype.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]
CASE = read_json(ROOT/'examples/rice_wheat.json')
TEMP = ROOT.parent/'.work/prototype_tests'
TEMP.mkdir(parents=True, exist_ok=True)

def action(name, args=None):
    return {'name': name, 'arguments': args or {}, 'rationale': '测试动作'}

class EngineeringTests(unittest.TestCase):
    def setUp(self):
        self.case = deepcopy(CASE)

    def solve(self, case=None, pump=None, motor=None):
        c = eng.validate_requirements(case or self.case)['normalized']
        loads = eng.calculate_loads(c)
        candidates = eng.match_hst(c, loads, pump, motor)['candidates']
        return eng.check_constraints(c, candidates)

    def test_missing_inputs_are_not_filled(self):
        result = eng.validate_requirements({'requirements': {'crop': 'rice_wheat'}})
        self.assertEqual(result['status'], 'missing_input')
        self.assertIn('requirements.working_mass_kg', result['missing'])
        self.assertNotIn('working_mass_kg', result['normalized']['requirements'])

    def test_units_and_nonmutating_validation(self):
        self.case['requirements']['working_mass_kg'] = {'value': 4.5, 'unit': 't'}
        self.case['scenarios'][1]['speed_m_s'] = {'value': 6.48, 'unit': 'km/h'}
        self.case['requirements']['drive_sprocket_effective_radius_m'] = {'value': 250, 'unit': 'mm'}
        result = eng.validate_requirements(self.case)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['normalized']['requirements']['working_mass_kg'], 4500)
        self.assertAlmostEqual(result['normalized']['scenarios'][1]['speed_m_s'], 1.8)
        self.assertIsInstance(self.case['requirements']['working_mass_kg'], dict)

    def test_nonfinite_boolean_and_zero_rejected(self):
        for value in [0, -1, True, float('inf'), float('nan'), {'value': 1e308, 'unit': 't'}]:
            with self.subTest(value=value):
                self.case = deepcopy(CASE)
                self.case['requirements']['working_mass_kg'] = value
                self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_invalid_efficiency(self):
        for value in [0, 1.1]:
            self.case['efficiencies']['pump_volumetric'] = value
            self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_unit_mismatch(self):
        self.case['scenarios'][0]['speed_m_s'] = {'value': 5, 'unit': 'kg'}
        self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_unsupported_regimes(self):
        for key, value in [('speed_m_s', -1), ('slope_deg', -3), ('acceleration_m_s2', .1)]:
            c = deepcopy(CASE)
            c['scenarios'][0][key] = value
            self.assertEqual(eng.validate_requirements(c)['status'], 'model_not_applicable')

    def test_duplicate_scenario_id(self):
        self.case['scenarios'][1]['id'] = self.case['scenarios'][0]['id']
        self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_malformed_sections(self):
        for field, value in [('requirements', []), ('efficiencies', 'bad'), ('scenarios', [4]), ('illustrative_catalog', 4)]:
            c = deepcopy(CASE); c[field] = value
            self.assertEqual(eng.validate_requirements(c)['status'], 'invalid_input')
        self.case['requirements']['required_turn_modes'] = 3
        self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_power_budget_cannot_exceed_engine(self):
        self.case['requirements']['travel_shaft_power_budget_kw'] = 200
        self.assertEqual(eng.validate_requirements(self.case)['status'], 'invalid_input')

    def test_baseline_numerics_and_candidate(self):
        result = self.solve()
        self.assertEqual(result['selected']['candidate_id'], 'P56_M55')
        self.assertAlmostEqual(result['selected']['scenarios'][1]['motor_inlet_flow_l_min_each'], 113.0346085, places=6)
        self.assertAlmostEqual(result['selected']['scenarios'][2]['pressure_mpa'], 7.7831164, places=6)

    def test_revision_exposes_old_flow_failure(self):
        self.case['scenarios'][1]['speed_m_s'] = 2.2
        old = self.solve(pump=56, motor=55)
        self.assertEqual(old['status'], 'constraint_failed')
        self.assertEqual([x['constraint'] for x in old['candidates'][0]['failures']], ['flow'])
        self.assertAlmostEqual(old['candidates'][0]['failures'][0]['required'], 138.1534104, places=6)
        self.assertEqual(self.solve()['selected']['candidate_id'], 'P63_M55')

    def test_power_balance_and_mass_dependence(self):
        result = self.solve()['selected']['scenarios']
        efficiency = prod(self.case['efficiencies'].values())
        for scenario, row in zip(self.case['scenarios'], result):
            self.assertAlmostEqual(row['pump_shaft_power_kw_total'], row['design_force_n']*scenario['speed_m_s']/1000/efficiency)
        self.case['requirements']['working_mass_kg'] *= 2
        new = self.solve(pump=56, motor=55)['candidates'][0]['scenarios'][0]
        self.assertAlmostEqual(new['pressure_mpa'], 2*result[0]['pressure_mpa'])
        self.assertAlmostEqual(new['motor_inlet_flow_l_min_each'], result[0]['motor_inlet_flow_l_min_each'])

    def test_no_feasible_candidate_is_explicit(self):
        self.case['requirements']['travel_shaft_power_budget_kw'] = 1
        result = self.solve()
        self.assertEqual(result['status'], 'constraint_failed')
        self.assertIsNone(result['selected'])

    def test_unimplemented_configuration_not_silently_replaced(self):
        self.assertEqual(eng.check_configuration('dual_power_stream', CASE['requirements'])['status'], 'model_not_applicable')
        self.assertEqual(eng.check_configuration('single_hst_brake', CASE['requirements'])['status'], 'constraint_failed')

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)/'run'
        self.flow = Workflow.create(self.run, '稻麦机，左右模块化，支持原地转向')

    def call(self, name, args=None):
        return self.flow.execute(action(name, args), actor='unit_test')

    def prepare(self, case=None):
        self.flow.supply(case or CASE, 'demo_script', '测试输入')
        self.call('validate_requirements')
        self.flow.review('G1', 'accept', 'demo_script', '测试确认')
        self.call('check_configuration', {'configuration_id': 'independent_hst'})
        self.flow.review('G2', 'accept', 'demo_script', '测试确认')

    def solve(self):
        self.call('calculate_loads'); self.call('match_hst'); return self.call('check_constraints')

    def test_gates_block_early_calculation_and_final_approval(self):
        self.assertEqual(self.call('calculate_loads')['status'], 'invalid_input')
        with self.assertRaises(ValueError):
            self.flow.review('G3', 'accept', 'human', '不能跳过计算')

    def test_model_cannot_approve_or_override_constraints(self):
        self.assertEqual(self.call('review', {'gate': 'G1', 'decision': 'accept'})['status'], 'invalid_input')
        self.assertEqual(self.flow.state['review_log'], [])
        self.assertEqual(self.call('check_constraints', {'pressure_limit': 999})['status'], 'invalid_input')

    def test_input_update_is_transactional(self):
        self.prepare()
        before = deepcopy(self.flow.state['case'])
        version = self.flow.state['version']
        result = self.flow.supply({'efficiencies': {'pump_volumetric': 0}}, 'human', '无效')
        self.assertEqual(result['status'], 'invalid_input')
        self.assertEqual(self.flow.state['case'], before)
        self.assertEqual(self.flow.state['version'], version)

    def test_revision_invalidates_old_checks_gates_and_latest_report(self):
        self.prepare(); self.solve(); self.call('build_report')
        old_version = self.flow.state['version']
        self.flow.revise('transfer_flat', 'speed_m_s', 2.2, 'demo_script', '提速')
        self.assertEqual(self.flow.state['version'], old_version+1)
        self.assertIsNone(self.flow.state['selected'])
        self.assertEqual(self.flow.state['gates'], {})
        self.assertEqual(self.call('build_report')['status'], 'invalid_input')
        self.assertIn('当前方案待更新', (self.run/'design_report.md').read_text(encoding='utf-8'))
        self.assertTrue((self.run/f'design_report_v{old_version}.md').exists())

    def test_draft_and_accepted_report_are_distinguished(self):
        self.prepare(); self.solve(); self.call('build_report')
        self.assertIn('待 G3 审核', (self.run/'design_report.md').read_text(encoding='utf-8'))
        self.flow.review('G3', 'accept', 'demo_script', '预设')
        self.call('build_report')
        self.assertIn('已接受初步方案', (self.run/'design_report.md').read_text(encoding='utf-8'))
        self.assertIn('demo_script', (self.run/'design_report.md').read_text(encoding='utf-8'))

    def test_three_failures_stop_and_question_cannot_bypass(self):
        c = deepcopy(CASE); c['requirements']['travel_shaft_power_budget_kw'] = 1
        self.prepare(c); self.call('calculate_loads'); self.call('match_hst')
        for _ in range(3):
            self.call('check_constraints')
        self.assertEqual(self.flow.state['status'], 'stopped_needs_review')
        self.call('ask_engineer', {'message': '需要取舍'})
        self.assertEqual(self.call('match_hst')['status'], 'invalid_input')
        self.assertEqual(self.call('build_report')['status'], 'invalid_input')

    def test_trace_and_reload(self):
        self.call('validate_requirements')
        flow = Workflow(self.run)
        self.assertEqual(flow.state['tool_sequence'], 1)
        log = json.loads((self.run/'tool_calls.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(log['actor'], 'unit_test')
        self.assertEqual(log['result']['status'], 'missing_input')

    def test_unknown_case_state_injection_rejected(self):
        self.assertEqual(self.call('set_requirements', {'patch': {'gates': {'G3': 'accept'}}})['status'], 'invalid_input')

    def test_existing_run_not_overwritten(self):
        with self.assertRaises(ValueError):
            Workflow.create(self.run, '覆盖')

    def test_replay_end_to_end_is_labelled(self):
        path = Path(self.temp.name)/'demo'
        result = run_demo(path)
        self.assertEqual(result['selected'], 'P63_M55')
        self.assertEqual(result['mode'], 'demo_replay')
        self.assertIn('固定脚本演示', (path/'design_report.md').read_text(encoding='utf-8'))

    def test_demo_cli_prints_string_selection_successfully(self):
        path = Path(self.temp.name)/'cli_demo'
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['demo', '--run', str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['selected'], 'P63_M55')

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(self.temp.cleanup)
        self.flow = Workflow.create(Path(self.temp.name)/'api', '示例输入', 'api')

    def test_missing_key_fails_without_network(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            run_api(self.flow, 'example-model')

    def test_api_contract_and_human_pause_with_mock_transport(self):
        operations = [('set_requirements', {'patch': CASE}), ('validate_requirements', {})]
        bodies = []
        def transport(base, key, body):
            bodies.append(body)
            operation, args = operations[len(bodies)-1]
            return {'id': 'mock-response', 'status': 'completed', 'model': 'mock-only', 'output': [{'type': 'function_call', 'name': 'design_action', 'call_id': f'mock-{len(bodies)}', 'arguments': json.dumps({'operation': operation, 'arguments_json': json.dumps(args), 'rationale': '仅用于适配器测试'})}]}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret-not-real'}):
            result = run_api(self.flow, 'mock-only', transport=transport)
        self.assertEqual(result['status'], 'needs_review_G1')
        self.assertEqual(len(bodies), 2)
        self.assertFalse(bodies[0]['parallel_tool_calls'])
        self.assertNotIn('test-secret-not-real', (self.flow.directory/'llm_events.jsonl').read_text(encoding='utf-8'))

    def test_incomplete_response_executes_nothing(self):
        with self.assertRaises(RuntimeError):
            run_api(self.flow, 'mock-only', transport=lambda *args: {'status': 'incomplete', 'output': []})
        self.assertEqual(self.flow.state['tool_sequence'], 0)

    def test_insecure_remote_url_rejected(self):
        with self.assertRaises(ValueError):
            post_response('http://example.org/v1', 'test-key', {})

if __name__ == '__main__':
    unittest.main()
