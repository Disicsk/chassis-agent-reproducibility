"""Persistent workflow. LLM actions cannot approve human review gates."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from . import __version__
from . import engineering as eng
from .reporting import markdown, html_report

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()

def write_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)

def merge(old, patch):
    result = deepcopy(old)
    for k, value in patch.items():
        result[k] = merge(result.get(k, {}), value) if isinstance(value, dict) and isinstance(result.get(k, {}), dict) else deepcopy(value)
    return result

def differences(old, new, prefix=''):
    if isinstance(old, dict) and isinstance(new, dict):
        return [item for key in sorted(set(old) | set(new)) for item in differences(old.get(key), new.get(key), f'{prefix}.{key}' if prefix else key)]
    if isinstance(old, list) and isinstance(new, list) and all(isinstance(x, dict) and 'id' in x for x in old+new):
        return differences({x['id']:x for x in old}, {x['id']:x for x in new}, prefix)
    return [] if old == new else [{'path': prefix, 'old': old, 'new': new}]

ALLOWED_CASE_KEYS = {'case_id', 'title', 'design_family', 'planetary', 'requirements', 'efficiencies', 'design_limits', 'scenarios', 'illustrative_catalog', 'parameter_provenance'}
OPERATIONS = ['set_requirements', 'validate_requirements', 'ask_engineer', 'check_configuration', 'calculate_loads', 'match_hst', 'check_constraints', 'build_report', 'stop']
ARGUMENT_KEYS = {'set_requirements': {'patch', 'note'}, 'validate_requirements': set(),
    'ask_engineer': {'message'}, 'check_configuration': {'configuration_id'}, 'calculate_loads': set(),
    'match_hst': {'pump_ml_rev', 'motor_ml_rev', 'branch_selection'}, 'check_constraints': set(), 'build_report': set(), 'stop': {'reason'}}

def parameter_ledger(case, default_origin):
    provenance = case.get('parameter_provenance', {})
    reference = provenance.get('source_reference_or_rounded', [])
    adapted = provenance.get('adapted_reference_with_assumed_meaning', {})
    assumed = provenance.get('author_assumptions', [])
    rows = []
    for group, defs in [('requirements', {**eng.REQ, **eng.OPTIONAL_REQ}), ('efficiencies', eng.EFF), ('design_limits', eng.LIMITS), ('planetary', eng.PLANETARY)]:
        for key, (unit, _, _) in defs.items():
            if key not in case.get(group, {}):
                continue
            origin = 'reference' if key in reference else 'assumption' if key in adapted or key in assumed or group in assumed else default_origin
            rows.append({'path': f'{group}.{key}', 'value': case[group][key], 'unit': unit,
                'origin': origin, 'role': 'bound' if group == 'design_limits' else 'fixed',
                'note': adapted.get(key, '算例设定' if origin == 'assumption' else '')})
    return rows


def update_parameter_ledger(case, previous, origin, decided_by=None, executed_by=None):
    """Retain provenance of unchanged values; authorship is not edit execution."""
    prior = {row['path']: row for row in previous}
    rows = parameter_ledger(case, origin)
    for i, row in enumerate(rows):
        old = prior.get(row['path'])
        if old is not None and old['value'] == row['value']:
            rows[i] = deepcopy(old)
            continue
        if old is not None:
            row['origin'] = origin
            row['original_origin'] = old.get('original_origin', old['origin'])
            row['previous_value'] = old['value']
        else:
            row['original_origin'] = row['origin']
        row['decided_by'] = decided_by or origin
        row['executed_by'] = executed_by or origin
    return rows

class Workflow:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.state = json.loads((self.directory/'state.json').read_text(encoding='utf-8'))

    @classmethod
    def create(cls, directory, request, mode='external_llm', model_id=None):
        directory = Path(directory).resolve()
        if mode not in ['external_llm', 'demo_replay', 'api']:
            raise ValueError('未知运行方式')
        directory.mkdir(parents=True, exist_ok=True)
        if (directory/'state.json').exists():
            raise ValueError('此运行目录已有状态，请使用新目录或继续原任务')
        (directory/'snapshots').mkdir(exist_ok=True)
        source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
        prompt = Path(__file__).parent/'prompts/system.md'
        state = {'task_id': directory.name, 'version': 0, 'request': request, 'case': {},
            'metadata': {'mode': mode, 'model_id': model_id, 'created_at': now(), 'prototype_version': __version__, 'code_sha256': source_hashes, 'prompt_sha256': hashlib.sha256(prompt.read_bytes()).hexdigest()},
            'status': 'collecting_requirements', 'validated_version': None, 'gates': {}, 'configuration': None,
            'loads': None, 'matches': None, 'checked': None, 'selected': None, 'checked_version': None,
            'question': None, 'parameter_ledger': [], 'review_log': [], 'changes': [], 'check_history': [],
            'previous_design': None, 'failure_attempts': 0, 'tool_sequence': 0, 'last_tool_result': None}
        write_json(directory/'state.json', state)
        flow = cls(directory)
        flow.event('task_created', {'request': request, 'mode': mode})
        return flow

    def append(self, name, obj):
        with (self.directory/name).open('a', encoding='utf-8') as file:
            file.write(json.dumps(obj, ensure_ascii=False, allow_nan=False)+'\n')

    def event(self, kind, payload):
        self.append('events.jsonl', {'time': now(), 'kind': kind, 'version': self.state['version'], **payload})

    def save(self):
        write_json(self.directory/'state.json', self.state)
        write_json(self.directory/'requirements.json', self.state['case'])
        write_json(self.directory/'review_log.json', self.state['review_log'])

    def invalidate_report(self, reason):
        if (self.directory/'design_report.md').exists():
            message = f"# 当前方案待更新\n\n{reason}\n\n当前输入版本：{self.state['version']}。历史报告保存在带版本号的文件中，不能作为当前方案。\n"
            (self.directory/'design_report.md').write_text(message, encoding='utf-8')
            (self.directory/'design_report.html').write_text(html_report(message), encoding='utf-8')

    def require_gate(self, gate):
        record = self.state['gates'].get(gate)
        if not record or record['version'] != self.state['version'] or record['decision'] != 'accept':
            raise ValueError(f'需要独立审核入口确认 {gate}；LLM 不能代替工程师批准')

    def replace_case(self, patch, origin, instruction):
        if not isinstance(patch, dict) or set(patch)-ALLOWED_CASE_KEYS:
            raise ValueError('输入补丁包含未知顶层字段；不得写入计算、审核或参考答案')
        new_case = merge(self.state['case'], patch)
        validation = eng.validate_requirements(new_case)
        if validation['status'] in ['invalid_input', 'model_not_applicable']:
            return validation
        new_case = validation['normalized']
        old = deepcopy(self.state)
        if old['case'] == new_case:
            return {'status': 'ok', 'note': '输入未变化', 'version': old['version']}
        if old['version']:
            write_json(self.directory/'snapshots'/f"v{old['version']}_before_update.json", old)
        if old['selected']:
            self.state['previous_design'] = {'version': old['version'], 'selected': old['selected'], 'case': old['case']}
        self.state['version'] += 1
        if old['selected'] or old['previous_design']:
            self.state['changes'].append({'from_version': old['version'], 'to_version': self.state['version'], 'instruction': instruction, 'fields': differences(old['case'], new_case)})
        ledger = update_parameter_ledger(new_case, self.state['parameter_ledger'], origin)
        self.state.update({'case': new_case, 'parameter_ledger': ledger, 'validated_version': None,
            'gates': {}, 'configuration': None, 'loads': None, 'matches': None, 'checked': None, 'selected': None,
            'checked_version': None, 'question': None, 'status': 'collecting_requirements', 'failure_attempts': 0})
        self.event('input_updated', {'origin': origin, 'instruction': instruction, 'case_sha256': digest(new_case)})
        self.invalidate_report('输入已经修改，原计算和审核失效。')
        return {'status': validation['status'], 'version': self.state['version'], 'missing': validation['missing'], 'invalidated': ['loads', 'matches', 'constraints', 'G1', 'G2', 'G3']}

    def supply(self, patch, mode, note):
        if mode not in ['human', 'demo_script']:
            raise ValueError('输入记录方式须为 human 或 demo_script')
        result = self.replace_case(patch, 'user' if mode == 'human' else 'assumption', note)
        self.event('engineer_input', {'mode': mode, 'note': note, 'result': result['status']})
        self.save()
        return result

    def review(self, gate, decision, mode, note):
        if gate not in ['G1', 'G2', 'G3'] or decision not in ['accept', 'reject'] or mode not in ['human', 'demo_script']:
            raise ValueError('无效审核字段')
        if gate == 'G1' and self.state['validated_version'] != self.state['version']:
            raise ValueError('先完成有效的需求校验')
        if gate == 'G2':
            self.require_gate('G1')
            if not self.state['configuration']:
                raise ValueError('先完成适用构型检查')
        if gate == 'G3':
            self.require_gate('G2')
            if self.state['checked_version'] != self.state['version'] or not self.state['selected']:
                raise ValueError('当前版本没有通过约束检查的方案')
        record = {'time': now(), 'gate': gate, 'version': self.state['version'], 'decision': decision, 'mode': mode, 'note': note}
        self.state['gates'][gate] = record
        self.state['review_log'].append(record)
        self.state['status'] = ('accepted' if gate == 'G3' else 'ready') if decision == 'accept' else 'needs_revision'
        if decision == 'reject':
            self.invalidate_report(f'{gate} 未被接受，请修订后重新审核。')
        self.event('review', {'record': record})
        self.save()
        return record

    def revise(self, scenario_id, field, value, mode, note):
        if not self.state['selected']:
            raise ValueError('先形成初步方案再进行演示修改')
        if field not in {**eng.SCENARIO, **eng.TURN_SCENARIO}:
            raise ValueError('仅允许修改具名工况参数')
        scenarios = deepcopy(self.state['case']['scenarios'])
        found = False
        for scenario in scenarios:
            if scenario['id'] == scenario_id:
                scenario[field] = value
                found = True
        if not found:
            raise ValueError('工况不存在')
        return self.supply({'scenarios': scenarios}, mode, note)

    def execute(self, action, actor='external_codex'):
        before = deepcopy(self.state)
        seq = before['tool_sequence'] + 1
        try:
            if not isinstance(action, dict) or set(action) != {'name', 'arguments', 'rationale'} or not isinstance(action['rationale'], str):
                raise ValueError('动作必须包含 name、arguments 和 rationale')
            name, args = action.get('name'), action.get('arguments', {})
            if name not in OPERATIONS or not isinstance(args, dict):
                raise ValueError('未知工具或无效参数；审核须使用独立入口')
            if set(args)-ARGUMENT_KEYS[name]:
                raise ValueError('工具参数包含未知字段')
            if (self.state['status'] == 'stopped_needs_review' or self.state['failure_attempts'] >= 3) and name not in ['build_report', 'stop', 'ask_engineer']:
                raise ValueError('自动修订已停止，需要工程师输入或取舍')
            result = self._dispatch(name, args)
            digest(result)  # Fail closed on non-finite computed quantities before saving state.
        except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
            self.state = before
            result = {'status': 'invalid_input', 'reason': str(exc)}
        self.state['tool_sequence'] = seq
        self.state['last_tool_result'] = {'name': action.get('name') if isinstance(action, dict) else None, 'result': result}
        record = {'call_id': f'call_{seq:03d}', 'time': now(), 'actor': actor, 'input_version': before['version'],
            'input_sha256': digest(before['case']), 'action': action, 'result': result, 'output_version': self.state['version']}
        self.append('tool_calls.jsonl', record)
        self.save()
        return result

    def _dispatch(self, name, args):
        s = self.state
        if name == 'set_requirements':
            return self.replace_case(args['patch'], 'llm_extracted_or_proposed', args.get('note', '模型整理输入，待 G1 确认'))
        if name == 'validate_requirements':
            result = eng.validate_requirements(s['case'])
            if result['status'] == 'ok':
                s['case'] = result['normalized']
                s['validated_version'] = s['version']
                if not s['gates'].get('G1'):
                    s['status'] = 'needs_review_G1'
            return result
        if name == 'ask_engineer':
            message = args.get('message')
            if not isinstance(message, str) or not message.strip():
                raise ValueError('补问内容为空')
            s['question'], s['status'] = message, 'awaiting_input'
            return {'status': 'needs_review', 'question': message}
        if name == 'stop':
            s['status'] = 'stopped_needs_review'
            return {'status': 'needs_review', 'reason': args.get('reason', '请求工程师处理未解决项')}
        self.require_gate('G1')
        if name == 'check_configuration':
            result = eng.check_configuration(args['configuration_id'], s['case']['requirements'], s['case'].get('design_family', 'independent_hst'))
            if result['status'] == 'ok':
                self.invalidate_report('构型已重新选择，需重新审核和计算。')
                s['configuration'] = result
                for key in ['loads', 'matches', 'checked', 'selected', 'checked_version']:
                    s[key] = None
                s['gates'].pop('G2', None)
                s['gates'].pop('G3', None)
                s['status'] = 'needs_review_G2'
            return result
        self.require_gate('G2')
        if name == 'calculate_loads':
            self.invalidate_report('计算已重新开始，请等待本轮完整约束检查及审核。')
            result = eng.calculate_loads(s['case'])
            s['loads'] = result
            s['status'] = 'computing'
            s['matches'] = s['checked'] = s['selected'] = s['checked_version'] = None
            s['gates'].pop('G3', None)
            return result
        if name == 'match_hst':
            if not s['loads']:
                raise ValueError('先计算当前版本负载')
            result = eng.match_hst(s['case'], s['loads'], args.get('pump_ml_rev'), args.get('motor_ml_rev'), args.get('branch_selection'))
            self.invalidate_report('候选已重新匹配，请等待约束检查和审核。')
            s['matches'] = result
            s['status'] = 'computing'
            s['checked'] = s['selected'] = s['checked_version'] = None
            s['gates'].pop('G3', None)
            return result
        if name == 'check_constraints':
            if not s['matches']:
                raise ValueError('先计算 HST 候选参数')
            result = eng.check_constraints(s['case'], s['matches']['candidates'])
            s['gates'].pop('G3', None)
            s['checked'], s['selected'], s['checked_version'] = result, result['selected'], s['version']
            s['check_history'].append({'version': s['version'], 'candidates': result['candidates']})
            if result['selected']:
                s['status'] = 'needs_review_G3'
                s['failure_attempts'] = 0
            else:
                s['failure_attempts'] += 1
                s['status'] = 'stopped_needs_review' if s['failure_attempts'] >= 3 else 'constraint_failed'
            return {**result, 'workflow_status': s['status'], 'failure_attempts': s['failure_attempts']}
        if name == 'build_report':
            if not s['selected'] or s['checked_version'] != s['version']:
                raise ValueError('没有当前版本的可行候选，不能生成通过报告')
            md = markdown(s)
            for stem in ['design_report', f"design_report_v{s['version']}"]:
                (self.directory/f'{stem}.md').write_text(md, encoding='utf-8')
                (self.directory/f'{stem}.html').write_text(html_report(md), encoding='utf-8')
            write_json(self.directory/'snapshots'/f"v{s['version']}_reported.json", s)
            return {'status': 'ok', 'report': str(self.directory/'design_report.md'), 'review_status': s['status']}
        raise ValueError('工具尚未实现')

    def context(self):
        return {'request': self.state['request'], 'version': self.state['version'], 'status': self.state['status'],
            'case': self.state['case'], 'gates': self.state['gates'], 'configuration': self.state['configuration'],
            'previous_design': self.state['previous_design'], 'last_tool_result': self.state['last_tool_result'],
            'available_operations': OPERATIONS, 'configuration_rules': eng.CONFIGURATIONS,
            'system_prompt': (Path(__file__).parent/'prompts/system.md').read_text(encoding='utf-8')}
