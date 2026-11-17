"""可重复的 App Server 夹具；不访问账户或网络。"""
import json
import os
from pathlib import Path
import sys
import time

if '--version' in sys.argv:
    print('codex-cli test-fixture')
    sys.exit()
root = Path(os.environ['FAKE_CODEX_ROOT'])
root.mkdir(parents=True, exist_ok=True)
thread = 'fake-thread'

def send(obj):
    print(json.dumps(obj), flush=True)

for line in sys.stdin:
    msg = json.loads(line); method = msg['method']; params = msg.get('params', {})
    if method == 'initialized':
        continue
    log = root / method.replace('/', '_')
    count = int(log.read_text()) + 1 if log.exists() else 1
    log.write_text(str(count))
    if method == 'account/rateLimits/read':
        fail_at = {int(item) for item in os.environ.get('FAKE_CODEX_QUOTA_ERROR_AT', '').split(',') if item}
        if count in fail_at:
            failure = os.environ.get('FAKE_CODEX_QUOTA_ERROR',
                'failed to fetch codex rate limits: error sending request for url (https://chatgpt.com/backend-api/wham/usage)')
            send({'id': msg['id'], 'error': {'code': -32603, 'message': failure}})
            continue
    if method == 'thread/resume':
        failure = os.environ.get('FAKE_CODEX_RESUME_ERROR')
        if count <= int(os.environ.get('FAKE_CODEX_THREAD_BUSY_ATTEMPTS', '0')):
            failure = f"thread {params['threadId']} already has an active writer"
        if failure:
            send({'id':msg['id'], 'error':{'code':-32603,'message':failure}})
            continue
    if method == 'initialize':
        result = {}
    elif method == 'account/read':
        result = {'account': {'type': 'chatgpt'}}
    elif method == 'account/rateLimits/read':
        resets = int(os.environ.get('FAKE_CODEX_QUOTA_CYCLES', '0'))
        exhausted = (count <= resets * 2 and count % 2 == 1) if resets else count == 1
        result = {'rateLimits': {'limitId': 'codex', 'primary': {
            'usedPercent': 100 if exhausted else 0,
            'resetsAt': time.time() + (.1 if resets else 1)},
            'secondary': {'usedPercent': 0}}}
    elif method in ('thread/start', 'thread/resume', 'thread/read'):
        thread = params.get('threadId', thread)
        active = method == 'thread/read' and count <= int(os.environ.get('FAKE_CODEX_THREAD_ACTIVE_READS', '0'))
        result = {'thread': {'id': thread, 'cwd': os.getcwd(), 'status': {'type': 'active' if active else 'idle'}}}
    elif method == 'turn/start':
        (root/'last_prompt.json').write_text(json.dumps(params),encoding='utf-8')
        failures = int(os.environ.get('FAKE_CODEX_QUOTA_FAILURES', '0'))
        turn = {'id': f'continued-{count}' if failures else 'continued',
                'status': 'failed' if count <= failures else 'completed'}
        if turn['status'] == 'failed':
            turn['error'] = {'codexErrorInfo': 'usageLimitExceeded', 'message': 'fixture quota exhausted'}
        rollout = os.environ.get('FAKE_CODEX_ROLLOUT')
        if rollout:
            rows = [{'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': turn['id']}},
                    {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': turn['id'],
                     **({'error': {'codex_error_info': 'usage_limit_exceeded'}} if turn['status'] == 'failed' else {})}}]
            with Path(rollout).open('a', encoding='utf-8') as stream:
                stream.write(''.join(json.dumps(row) + '\n' for row in rows))
        send({'id': msg['id'], 'result': {'turn': turn}})
        if turn['status'] == 'completed':
            send({'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn['id'],
                  'item': {'type': 'agentMessage', 'text': json.dumps({'status': 'complete', 'summary': 'PASS'})}}})
        send({'method': 'turn/completed', 'params': {'threadId': thread, 'turn': turn}})
        continue
    else:
        raise AssertionError(method)
    send({'id': msg['id'], 'result': result})
