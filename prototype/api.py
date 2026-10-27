"""Optional Responses API adapter; no credentials are stored in run files."""
import json
import os
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from .workflow import OPERATIONS, now

SCHEMA = {'type': 'function', 'name': 'design_action',
    'description': 'Execute one chassis design action. Review approvals are unavailable to the model.',
    'strict': True,
    'parameters': {'type': 'object', 'properties': {
        'operation': {'type': 'string', 'enum': OPERATIONS},
        'arguments_json': {'type': 'string', 'description': 'JSON-encoded object of arguments documented in the system prompt.'},
        'rationale': {'type': 'string', 'description': 'Brief decision basis, not hidden reasoning.'}},
        'required': ['operation', 'arguments_json', 'rationale'], 'additionalProperties': False}}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def post_response(base_url, api_key, body):
    parsed = urlparse(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('服务地址不得包含账号、密码、查询参数或片段')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ['localhost', '127.0.0.1', '::1']):
        raise ValueError('远程服务地址须使用 HTTPS')
    request = Request(base_url.rstrip('/')+'/responses',
        data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+api_key}, method='POST')
    try:
        with build_opener(NoRedirect).open(request, timeout=45) as response:
            return json.loads(response.read(20_000_000).decode('utf-8'))
    except HTTPError as exc:
        raise RuntimeError(f'模型接口 HTTP {exc.code}；请检查服务、模型名和本机凭据。未执行本轮工具。') from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError('模型接口连接失败或超时；已保留本地状态，可重试。') from None

def run_api(flow, model, base_url='https://api.openai.com/v1', key_env='OPENAI_API_KEY', max_steps=12, transport=None):
    if not model:
        raise ValueError('请通过 --model 或 OPENAI_MODEL 指定有权限使用的模型')
    key = os.environ.get(key_env)
    if not key and transport is None:
        raise ValueError(f'缺少环境变量 {key_env}；不要将密钥写入输入 JSON 或聊天')
    if flow.state['metadata']['mode'] != 'api':
        raise ValueError('请为 API 模式新建运行目录，避免混合运行证据')
    if not 1 <= max_steps <= 30:
        raise ValueError('每次 API 运行的步骤上限为 1—30')
    flow.state['metadata'].update({'requested_model': model, 'base_url': base_url, 'generation_settings': {'store': False, 'parallel_tool_calls': False, 'temperature': 'provider_default'}})
    flow.save()
    for step in range(max_steps):
        if flow.state['status'] in ['needs_review_G1', 'needs_review_G2', 'awaiting_input', 'stopped_needs_review', 'needs_revision']:
            return {'status': flow.state['status'], 'steps_this_call': step}
        context = flow.context()
        prompt = context.pop('system_prompt')
        body = {'model': model, 'instructions': prompt, 'input': [{'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}],
            'tools': [SCHEMA], 'tool_choice': 'required', 'parallel_tool_calls': False, 'store': False}
        # Each step is a new stateless request with current state and the last tool result.
        # No previous-response chain or hidden reasoning is synthesized by the prototype.
        response = (transport or post_response)(base_url, key, body)
        flow.append('llm_events.jsonl', {'time': now(), 'input_version': flow.state['version'], 'request': body, 'response': response})
        flow.state['metadata']['model_id'] = response.get('model', model)
        flow.save()
        calls = [x for x in response.get('output', []) if x.get('type') == 'function_call']
        if response.get('status', 'completed') != 'completed' or len(calls) != 1 or calls[0].get('name') != 'design_action':
            raise RuntimeError('模型未返回一个完整的 design_action 调用；原始响应已保存，未执行工具')
        try:
            wrapper = json.loads(calls[0]['arguments'])
            action = {'name': wrapper['operation'], 'arguments': json.loads(wrapper['arguments_json']), 'rationale': wrapper['rationale']}
            if not isinstance(action['arguments'], dict):
                raise ValueError('参数不是对象')
        except (KeyError, ValueError, TypeError):
            raise RuntimeError('模型动作参数无法解析；未执行工具') from None
        result = flow.execute(action, actor='model_api')
        if action['name'] == 'build_report':
            return {'status': flow.state['status'], 'steps_this_call': step+1, 'result': result}
    return {'status': 'step_limit', 'steps_this_call': max_steps, 'workflow_status': flow.state['status']}
