"""CLI and explicitly labelled offline demonstration."""
import argparse
from datetime import datetime
from pathlib import Path
import json
import os
import sys
from .workflow import Workflow

BASE = Path(__file__).parent

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f'非法数值 {x}')))

def compact(result):
    if not isinstance(result, dict):
        return result
    result = dict(result)
    result.pop('normalized', None)
    if 'candidates' in result:
        result['candidates'] = [{k: c[k] for k in ['candidate_id', 'passes_modeled_checks', 'failures'] if k in c} for c in result['candidates']]
    if isinstance(result.get('selected'), dict):
        result['selected'] = result['selected']['candidate_id']
    return result

def run_demo(directory, case_name='rice_wheat'):
    case = read_json(BASE/f'examples/{case_name}.json')
    request = (BASE/'examples'/('rapeseed_request.txt' if case_name == 'rapeseed' else 'request.txt')).read_text(encoding='utf-8')
    family = case.get('design_family', 'independent_hst')
    flow = Workflow.create(directory, request, 'demo_replay', 'none_fixed_script')
    def call(name, arguments=None):
        result = flow.execute({'name': name, 'arguments': arguments or {}, 'rationale': '固定演示脚本，用于复现软件流程；非模型决策'}, actor='fixed_demo_script')
        if result['status'] in ['invalid_input', 'model_not_applicable']:
            raise RuntimeError(str(result))
        return result
    call('validate_requirements')
    call('ask_engineer', {'message': '请补充质量、工况、行走功率、几何与传动边界，或接受已声明的算例假设。'})
    flow.supply(case, 'demo_script', '采用第一阶段已声明的示范输入')
    call('validate_requirements')
    flow.review('G1', 'accept', 'demo_script', '预设：接受算例输入及假设')
    call('check_configuration', {'configuration_id': family})
    flow.review('G2', 'accept', 'demo_script', '预设：接受算例所声明的构型')
    call('calculate_loads')
    call('match_hst')
    call('check_constraints')
    call('build_report')
    flow.review('G3', 'accept', 'demo_script', '预设：接受用于方法演示的初步方案')
    call('build_report')
    if case_name == 'rapeseed':
        return {'status': flow.state['status'], 'mode': 'demo_replay', 'selected': flow.state['selected']['candidate_id'], 'directory': str(flow.directory)}
    flow.revise('transfer_flat', 'speed_m_s', 2.2, 'demo_script', '预设修改：转场速度 1.8 → 2.2 m/s，其余条件不变')
    call('validate_requirements')
    flow.review('G1', 'accept', 'demo_script', '预设：确认速度变更')
    call('check_configuration', {'configuration_id': 'independent_hst'})
    flow.review('G2', 'accept', 'demo_script', '预设：保留左右独立双 HST')
    call('calculate_loads')
    previous = flow.state['previous_design']['selected']
    call('match_hst', {'pump_ml_rev': previous['pump_displacement_ml_rev_each'], 'motor_ml_rev': previous['motor_displacement_ml_rev_each']})
    result = call('check_constraints')
    if result['status'] == 'constraint_failed':
        call('match_hst')
        call('check_constraints')
    call('build_report')
    flow.review('G3', 'accept', 'demo_script', '预设：接受修改后的示范方案及未评价范围')
    call('build_report')
    return {'status': flow.state['status'], 'mode': 'demo_replay', 'selected': flow.state['selected']['candidate_id'], 'directory': str(flow.directory)}

def main(argv=None):
    parser = argparse.ArgumentParser(description='联合收获机 HST 底盘设计最小原型')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init', help='新建任务，不覆盖已有运行')
    p.add_argument('--run', required=True); p.add_argument('--request', required=True)
    p.add_argument('--mode', choices=['external_llm', 'api'], default='external_llm'); p.add_argument('--model')
    p = sub.add_parser('context', help='输出当前状态与模型动作说明'); p.add_argument('--run', required=True)
    p = sub.add_parser('act', help='执行模型提交的一项 JSON 动作'); p.add_argument('--run', required=True); p.add_argument('--action', required=True)
    p = sub.add_parser('supply', help='补充工程师输入'); p.add_argument('--run', required=True); p.add_argument('--case', required=True)
    p.add_argument('--mode', choices=['human', 'demo_script'], default='human'); p.add_argument('--note', required=True)
    p = sub.add_parser('review', help='独立人工审核入口'); p.add_argument('--run', required=True)
    p.add_argument('--gate', choices=['G1', 'G2', 'G3'], required=True); p.add_argument('--decision', choices=['accept', 'reject'], required=True)
    p.add_argument('--mode', choices=['human', 'demo_script'], default='human'); p.add_argument('--note', required=True)
    p = sub.add_parser('revise', help='修改一个工况参数'); p.add_argument('--run', required=True)
    p.add_argument('--scenario', required=True); p.add_argument('--field', required=True); p.add_argument('--value', type=float, required=True)
    p.add_argument('--mode', choices=['human', 'demo_script'], default='human'); p.add_argument('--note', required=True)
    p = sub.add_parser('api', help='运行 Responses API，遇审核节点暂停'); p.add_argument('--run', required=True)
    p.add_argument('--model', default=os.environ.get('OPENAI_MODEL')); p.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'))
    p.add_argument('--key-env', default='OPENAI_API_KEY'); p.add_argument('--steps', type=int, default=12)
    p = sub.add_parser('demo', help='离线固定脚本演示，不冒充模型实验')
    p.add_argument('--case', choices=['rice_wheat', 'rapeseed'], default='rice_wheat')
    p.add_argument('--run', default=str(BASE/'runs'/('demo_'+datetime.now().strftime('%Y%m%d_%H%M%S'))))
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            result = run_demo(args.run, args.case)
        elif args.command == 'init':
            flow = Workflow.create(args.run, Path(args.request).read_text(encoding='utf-8-sig'), args.mode, args.model)
            result = {'status': flow.state['status'], 'directory': str(flow.directory)}
        else:
            flow = Workflow(args.run)
            if args.command == 'context':
                result = flow.context()
            elif args.command == 'act':
                if flow.state['metadata']['mode'] != 'external_llm':
                    raise ValueError('act 文件桥只用于 external_llm 运行目录')
                result = flow.execute(read_json(args.action))
            elif args.command == 'supply':
                result = flow.supply(read_json(args.case), args.mode, args.note)
            elif args.command == 'review':
                result = flow.review(args.gate, args.decision, args.mode, args.note)
            elif args.command == 'revise':
                result = flow.revise(args.scenario, args.field, args.value, args.mode, args.note)
            elif args.command == 'api':
                from .api import run_api
                result = run_api(flow, args.model, args.base_url, args.key_env, args.steps)
        print(json.dumps(result if args.command == 'context' else compact(result), ensure_ascii=False, indent=2))
        return 2 if isinstance(result, dict) and result.get('status') in ['invalid_input', 'tool_error', 'model_not_applicable'] else 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
