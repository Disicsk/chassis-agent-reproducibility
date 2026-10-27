"""Preregistered 2 x 4 x 3 independent-context API workflow evaluation."""
import argparse
import csv
import hashlib
import json
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from .workflow import Workflow, write_json, now, digest
from . import engineering as eng
from .chat_api import load_config, public_settings, next_action, ModelServiceError

TASKS = ['complete', 'missing', 'revision', 'infeasible']
MISSING = ['working_mass_kg', 'travel_shaft_power_budget_kw']


def fixtures():
    root = Path(__file__).parent / 'examples'
    for family, filename in [('rice_wheat','rice_wheat.json'),('rapeseed','rapeseed.json')]:
        case = json.loads((root/filename).read_text(encoding='utf-8'))
        case.pop('parameter_provenance', None)
        case['case_id'] = family + '_api_evaluation'
        case['design_family'] = 'independent_hst' if family == 'rice_wheat' else 'dual_power_stream'
        for task in TASKS:
            full = deepcopy(case)
            if task == 'infeasible':full['requirements']['travel_shaft_power_budget_kw'] = 1.0
            visible = deepcopy(full)
            if task == 'missing':
                for key in MISSING:visible['requirements'].pop(key)
            revised = deepcopy(full)
            if task == 'revision':
                for s in revised['scenarios']:
                    if s['id'] == 'transfer_flat':s['speed_m_s'] = 2.2
            def oracle(c):
                result = eng.check_constraints(c, eng.match_hst(c, eng.calculate_loads(c))['candidates'])
                return result['selected']['candidate_id'] if result['selected'] else None
            yield {'family':family, 'task':task, 'initial_case':visible, 'full_case':full,
                   'revised_case':revised, 'expected_initial':oracle(full),
                   'expected_final':oracle(revised if task == 'revision' else full)}


def prepare(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    protocol={'version':'api-eval-2.0','created_at':now(),'model_config_entry':'deepseek_v4_1_flash',
      'design':'2 configurations x 4 task types x 3 repeated independent runs = 24 runs; no tuning on scored runs',
      'sampling':{'temperature':0.2,'thinking':'disabled','max_output_tokens':4096,'seed':'not set'},
      'max_actions_per_run':36,'network_retries_per_step':1,'concurrency':2,
      'review_policy':'Pre-scripted demo_script approvals after corresponding tool validation; not expert evaluation',
      'missing_input_policy':'Provide the two withheld requirement fields only after ask_engineer; do not reveal reference answers',
      'revision_policy':'After accepted initial report, externally revise transfer_flat.speed_m_s to 2.2; require old-candidate check then rematch when needed',
      'success':'Feasible task: correct model, expected candidate, current accepted report, unchanged authorized inputs; missing additionally asks before receiving values; revision additionally diagnoses failed old candidate. Infeasible: actual constraint failure then stop/ask, no accepted report.',
      'scoring':'Report task attainment and strict process attainment separately; invalid tool calls, unnecessary questions and unauthorized changes counted; failures retained',
      'scope':'Eight constructed tasks, three repeats each; repeated runs are not 24 independent engineering designs; no comparative efficiency claim',
      'context':'Each request contains current structured state, explicit completion flags, last 8 action statuses, fixed prompt and latest actual observation; no manuscript, historical answers or API key; candidate observations deterministically summarized',
      'development_note':'Two preliminary v1 runs exposed repeated validation/configuration checks. They were interrupted and retained separately before formal v2 evaluation; v2 adds completion flags, recent action statuses and a general no-redundant-revalidation instruction. No formal run is discarded or tuned.',
      'fixtures':list(fixtures())}
    target=out/'protocol.json'
    if target.exists():raise RuntimeError('Protocol already exists; do not overwrite')
    write_json(target,protocol)
    snapshot=out/'source_snapshot';snapshot.mkdir()
    for file in Path(__file__).parent.glob('*.py'):(snapshot/file.name).write_bytes(file.read_bytes())
    (snapshot/'system.md').write_bytes((Path(__file__).parent/'prompts/system.md').read_bytes())
    write_json(out/'source_hashes.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.iterdir()})
    print('Prepared 24 runs; expected outcomes: '+json.dumps([(x['family'],x['task'],x['expected_final']) for x in protocol['fixtures']]))


def run_one(out,spec,rep,config):
    name=f"{spec['family']}_{spec['task']}_r{rep}"
    directory=out/'runs'/name
    if (directory/'evaluation.json').exists():return json.loads((directory/'evaluation.json').read_text(encoding='utf-8'))
    if (directory/'state.json').exists():raise RuntimeError('Incomplete existing run; preserve it and investigate before restart')
    request=('请依据已提供的作业输入及构型偏好完成联合收获机 HST 底盘初步设计。'
             '必要输入缺失时集中补问；读取实际工具结果后决定下一步。保持给定约束和候选表。'
             '输入和方案通过独立工程师入口审核；在当前候选表内无法满足约束时说明原因并请求取舍。')
    f=Workflow.create(directory,request,mode='api',model_id=config['model'])
    f.supply(spec['initial_case'],'demo_script','预先冻结的实验输入；不包含候选答案')
    f.state['metadata'].update({'model_settings':public_settings(config),'experiment_task':spec['task'],'replicate':rep,
                               'generation_settings':{'temperature':0.2,'thinking':'disabled','max_output_tokens':4096}});f.save()
    revised=False;supplied=False;question_count=0;invalid=0;network_errors=0;schema_errors=0;wrong_model=0
    api_calls=0;elapsed=0.;prompt_tokens=0;completion_tokens=0;reasoning_tokens=0;seen_models=set();termination='step_limit'
    start=time.perf_counter()
    for step in range(36):
        status=f.state['status']
        if status in ['needs_review_G1','needs_review_G2']:
            f.review('G1' if status=='needs_review_G1' else 'G2','accept','demo_script','按冻结协议确认当前输入/构型，不提供下一步动作')
        if status=='stopped_needs_review':termination='stopped';break
        try:
            for attempt in range(2):
                try:
                    action,record=next_action(f,config);break
                except ModelServiceError as e:
                    message=str(e)
                    f.append('service_errors.jsonl',{'time':now(),'step':step,'attempt':attempt,'error':message})
                    if 'NETWORK' in message or 'HTTP_429' in message or 'HTTP_5' in message:
                        network_errors+=1
                        if attempt==0:continue
                    raise
        except ModelServiceError as e:
            termination=str(e)
            if 'ACTION' in termination:schema_errors+=1
            break
        api_calls+=1;elapsed+=record['latency_s'];seen_models.add(record['response']['model'])
        usage=record['response'].get('usage') or {}
        prompt_tokens+=usage.get('prompt_tokens',0);completion_tokens+=usage.get('completion_tokens',0)
        reasoning_tokens+=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens',0)
        result=f.execute(action,actor='model_api_chat')
        if result.get('status') in ['invalid_input','model_not_applicable']:invalid+=1
        if action['name']=='check_configuration' and action['arguments'].get('configuration_id')!=spec['full_case']['design_family']:wrong_model+=1
        if action['name']=='ask_engineer':
            question_count+=1
            if spec['task']=='missing' and not supplied:
                f.supply({'requirements':{k:spec['full_case']['requirements'][k] for k in MISSING}},'demo_script','按冻结协议回复缺失的质量与行走功率预算')
                supplied=True
            else:termination='asked_for_decision';break
        elif action['name']=='stop':termination='stopped';break
        elif action['name']=='build_report' and result.get('status')=='ok':
            if f.state['status']=='needs_review_G3':
                f.review('G3','accept','demo_script','仅按冻结协议检查当前候选通过已设约束，非专家评估')
            elif f.state['status']=='accepted':
                if spec['task']=='revision' and not revised:
                    f.revise('transfer_flat','speed_m_s',2.2,'demo_script','转场速度由1.8提高至2.2 m/s，其他条件不变；请检查原候选能否沿用')
                    revised=True
                else:termination='accepted_report';break
        print(f"{name}: {step+1:02d} {action['name']} -> {f.state['status']}",flush=True)
    calls=[json.loads(line) for line in (directory/'tool_calls.jsonl').read_text(encoding='utf-8').splitlines()] if (directory/'tool_calls.jsonl').exists() else []
    selected=(f.state.get('selected') or {}).get('candidate_id')
    reference=spec['revised_case'] if revised else spec['full_case']
    inputs_unchanged=eng.validate_requirements(f.state['case'])['normalized']==eng.validate_requirements(reference)['normalized']
    checked_failure=any(x['action']['name']=='check_constraints' and x['result'].get('status')=='constraint_failed' for x in calls)
    old_failed=any(len(x['candidates'])==1 and not x['candidates'][0].get('passes_modeled_checks') for x in f.state['check_history'])
    report_current=termination=='accepted_report' and f.state['checked_version']==f.state['version']
    if spec['task']=='infeasible':success=selected is None and checked_failure and termination in ['stopped','asked_for_decision'] and inputs_unchanged
    else:
        success=selected==spec['expected_final'] and report_current and inputs_unchanged and wrong_model==0
        if spec['task']=='missing':success=success and supplied and question_count>=1
        if spec['task']=='revision':success=success and revised and old_failed
    summary={'run_id':name,'family':spec['family'],'task':spec['task'],'replicate':rep,'success':bool(success),
       'strict_process_success':bool(success and invalid==0 and schema_errors==0),'termination':termination,
       'selected':selected,'expected_selected':spec['expected_final'],'inputs_unchanged':inputs_unchanged,
       'revised':revised,'old_candidate_failure_observed':old_failed,'missing_values_requested':supplied,
       'question_count':question_count,'invalid_tool_calls':invalid,'wrong_model_calls':wrong_model,'schema_errors':schema_errors,
       'network_errors':network_errors,'model_calls':api_calls,'observed_models':sorted(seen_models),
       'prompt_tokens':prompt_tokens,'completion_tokens':completion_tokens,'reasoning_tokens':reasoning_tokens,
       'api_latency_s':round(elapsed,3),'wall_time_s':round(time.perf_counter()-start,3),'source':'actual_network_api',
       'review_mode':'demo_script','finished_at':now()}
    write_json(directory/'evaluation.json',summary)
    print('RESULT '+json.dumps(summary,ensure_ascii=False),flush=True)
    return summary


def run(out,config_path):
    out=Path(out);protocol=json.loads((out/'protocol.json').read_text(encoding='utf-8'));config=load_config(config_path)
    write_json(out/'model_settings.json',public_settings(config))
    jobs=[(spec,rep) for rep in [1,2,3] for spec in protocol['fixtures']]
    results=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run_one,out,spec,rep,config) for spec,rep in jobs]
        for future in as_completed(futures):
            results.append(future.result());write_json(out/'results_partial.json',sorted(results,key=lambda x:x['run_id']))
    results.sort(key=lambda x:x['run_id']);write_json(out/'results.json',results)
    with (out/'results.csv').open('w',newline='',encoding='utf-8-sig') as file:
        w=csv.DictWriter(file,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
    summary={'runs':len(results),'successes':sum(x['success'] for x in results),
             'strict_process_successes':sum(x['strict_process_success'] for x in results),
             'model_calls':sum(x['model_calls'] for x in results),'prompt_tokens':sum(x['prompt_tokens'] for x in results),
             'completion_tokens':sum(x['completion_tokens'] for x in results),'reasoning_tokens':sum(x['reasoning_tokens'] for x in results),
             'invalid_tool_calls':sum(x['invalid_tool_calls'] for x in results),'schema_errors':sum(x['schema_errors'] for x in results),
             'network_errors':sum(x['network_errors'] for x in results),'finished_at':now()}
    write_json(out/'summary.json',summary);print('COMPLETE '+json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','run']);p.add_argument('--out',required=True);p.add_argument('--config',default='models.toml');a=p.parse_args()
    if a.command=='prepare':prepare(a.out)
    else:run(a.out,a.config)
