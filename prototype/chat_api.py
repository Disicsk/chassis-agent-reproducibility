"""Configured Chat Completions adapter. Credentials never enter task context/logs."""
import json
import time
import tomllib
from collections import Counter
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from .api import NoRedirect


class ModelServiceError(RuntimeError):
    pass


def load_config(path, name='deepseek_v4_1_flash'):
    try:
        value = tomllib.loads(Path(path).read_text(encoding='utf-8-sig'))['models'][name]
    except Exception:
        raise ModelServiceError('Unable to parse requested model configuration') from None
    if value.get('api_mode') != 'chat_completions' or not value.get('api_key'):
        raise ModelServiceError('Configured API mode or credential is unavailable')
    u = urlsplit(value['base_url'])
    if u.scheme != 'https' or u.username or u.password or u.query or u.fragment:
        raise ModelServiceError('Model endpoint must be a credential-free HTTPS URL')
    return value


def public_settings(config):
    return {k: config.get(k) for k in ['provider', 'model', 'base_url', 'api_mode',
             'structured_output_mode', 'allowed_response_models', 'max_output_tokens',
             'timeout_sec', 'retries']}


def endpoint(config):
    base = config['base_url'].rstrip('/')
    return base + ('/chat/completions' if base.endswith('/v1') else '/v1/chat/completions')


def complete(config, messages, max_tokens=None):
    body = {'model': config['model'], 'messages': messages,
            'response_format': {'type': 'json_object'}, 'temperature': 0.2,
            'thinking': {'type': 'disabled'}, 'stream': False,
            'max_tokens': max_tokens or config.get('max_output_tokens', 4096)}
    encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
    # Read only for authorization; never place config, headers or credentials in a log.
    request = Request(endpoint(config), data=encoded,
                      headers={'Content-Type': 'application/json',
                               'Authorization': 'Bearer ' + config['api_key']}, method='POST')
    start = time.perf_counter()
    try:
        with build_opener(NoRedirect).open(request, timeout=config.get('timeout_sec', 180)) as res:
            raw = res.read(20_000_000).decode('utf-8')
        if config['api_key'] in raw:
            raise ModelServiceError('Credential unexpectedly appeared in response; response discarded')
        response = json.loads(raw)
    except HTTPError as exc:
        raise ModelServiceError(f'HTTP_{exc.code}; response body omitted') from None
    except (URLError, TimeoutError, OSError):
        raise ModelServiceError('NETWORK_OR_TIMEOUT; details omitted') from None
    except (ValueError, UnicodeError):
        raise ModelServiceError('INVALID_RESPONSE_JSON') from None
    actual = response.get('model')
    if actual not in config.get('allowed_response_models', [config['model']]):
        raise ModelServiceError('UNEXPECTED_RESPONSE_MODEL; no action executed')
    choices = response.get('choices', [])
    if len(choices) != 1:
        raise ModelServiceError('EXPECTED_ONE_CHOICE')
    item = choices[0]
    record = {'request': body, 'response': {'id': response.get('id'), 'model': actual,
              'created': response.get('created'), 'system_fingerprint': response.get('system_fingerprint'),
              'usage': response.get('usage'), 'finish_reason': item.get('finish_reason'),
              'content': item.get('message', {}).get('content')},
              'latency_s': round(time.perf_counter()-start, 3)}
    return record


def compact_context(flow):
    """Bound observations without changing selected candidates or hiding constraint types."""
    c = flow.context()
    s = flow.state
    c['execution_progress'] = {
        'input_validated_for_current_version': s.get('validated_version') == s['version'],
        'configuration_confirmed_for_current_version': bool(s.get('configuration')) and bool(s.get('gates', {}).get('G2')),
        'loads_calculated_for_current_version': s.get('loads') is not None,
        'candidates_calculated_for_current_version': s.get('matches') is not None,
        'constraints_checked_for_current_version': s.get('checked_version') == s['version'],
        'selected_candidate_id': (s.get('selected') or {}).get('candidate_id'),
        'report_written_for_current_version': (flow.directory/f"design_report_v{s['version']}.md").exists(),
    }
    log = flow.directory/'tool_calls.jsonl'
    if log.exists():
        c['recent_actions'] = [{'name':x['action']['name'],'input_version':x['input_version'],
                                'status':x['result'].get('status')} for x in
                               [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()[-8:]]]
    last = c.get('last_tool_result')
    if last and last['name'] in ['match_hst', 'check_constraints']:
        r = last['result']
        candidates = r.get('candidates', [])
        if candidates:
            compact = {k: v for k, v in r.items() if k != 'candidates'}
            compact['candidate_count'] = len(candidates)
            compact['candidate_ids'] = [x.get('candidate_id') for x in candidates]
            if last['name'] == 'check_constraints':
                failures = Counter()
                for x in candidates:
                    for item in x.get('failures', []):
                        failures[str(item.get('constraint', item.get('check', 'unknown')))] += 1
                compact['failed_constraint_counts'] = dict(failures)
                # A failed original candidate needs its exact requirement and limit.
                compact['candidate_details'] = candidates[:1] if len(candidates) == 1 else []
                compact['failed_examples'] = [x for x in candidates if not x.get('passes_modeled_checks', False)][:2]
            c['last_tool_result'] = {'name': last['name'], 'result': compact}
    return c


def next_action(flow, config):
    context = compact_context(flow)
    prompt = context.pop('system_prompt')
    prompt = prompt.replace('arguments={"pump_ml_rev":56,"motor_ml_rev":55} 只计算某个候选',
                            'arguments 中以数值字段 pump_ml_rev 和 motor_ml_rev 指定当前候选表中的某个组合')
    prompt += ('\nexecution_progress 显式记录当前版本已经完成的工作。输入未修改时不要重复已通过的校验或已确认的构型检查，'
               '它们会清空后续结果或重复请求审核。当前 case 是外部已提供的输入，不必复制写回；只有有依据的新输入才使用 set_requirements。'
               '结合阶段标志、审核记录和最近实际动作选择尚未完成的工作；不得将工具动作代替独立审核。')
    prompt += '\n本接口使用 JSON 输出模式。只返回一个 JSON 对象，严格包含 name、arguments、rationale，使用上面的动作格式。不要返回 Markdown 或 design_action 包装。'
    record = complete(config, [{'role': 'system', 'content': prompt},
                               {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}])
    record['input_version'] = flow.state['version']
    flow.append('llm_events.jsonl', record)
    response = record['response']
    if response['finish_reason'] != 'stop':
        raise ModelServiceError('INCOMPLETE_MODEL_ACTION')
    try:
        action = json.loads(response['content'])
        if not isinstance(action, dict) or set(action) != {'name', 'arguments', 'rationale'}:
            raise ValueError()
        if not isinstance(action['arguments'], dict) or not isinstance(action['rationale'], str):
            raise ValueError()
    except (TypeError, ValueError):
        raise ModelServiceError('INVALID_ACTION_SCHEMA') from None
    return action, record
