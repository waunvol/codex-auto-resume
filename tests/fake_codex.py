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
    if method == 'initialize':
        result = {}
    elif method == 'account/read':
        result = {'account': {'type': 'chatgpt'}}
    elif method == 'account/rateLimits/read':
        result = {'rateLimits': {'limitId': 'codex', 'primary': {
            'usedPercent': 100 if count == 1 else 0, 'resetsAt': time.time() + 1},
            'secondary': {'usedPercent': 0}}}
    elif method in ('thread/start', 'thread/resume', 'thread/read'):
        thread = params.get('threadId', thread)
        result = {'thread': {'id': thread, 'cwd': os.getcwd(), 'status': {'type': 'idle'}}}
    elif method == 'turn/start':
        (root/'last_prompt.json').write_text(json.dumps(params),encoding='utf-8')
        turn = {'id': 'continued', 'status': 'completed'}
        send({'id': msg['id'], 'result': {'turn': turn}})
        send({'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn['id'],
              'item': {'type': 'agentMessage', 'text': json.dumps({'status': 'complete', 'summary': 'PASS'})}}})
        send({'method': 'turn/completed', 'params': {'threadId': thread, 'turn': turn}})
        continue
    else:
        raise AssertionError(method)
    send({'id': msg['id'], 'result': result})
