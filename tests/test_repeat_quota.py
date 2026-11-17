"""连续额度周期与同一聊天后续轮次的恢复，不访问真实账户。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import codex_auto_resume as runner

FAKE = Path(__file__).with_name('fake_codex.py')
HOOK = Path(__file__).resolve().parents[1] / 'src/codex_resume_hook.py'


def append_event(path, payload):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'event_msg', 'payload': payload}) + '\n')


def wait_for(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Expected recovery state not reached')


class RepeatQuotaTests(unittest.TestCase):
    def environment(self, root, rollout):
        return {'CODEX_HOME': str(root / 'home'), 'CODEX_RESUME_HOOK_ROOT': str(root / 'jobs'),
                'FAKE_CODEX_ROOT': str(root / 'calls'),
                'CODEX_RESUME_CODEX_COMMAND': json.dumps([sys.executable, str(FAKE)]),
                'FAKE_CODEX_QUOTA_CYCLES': '3', 'FAKE_CODEX_QUOTA_FAILURES': '2',
                'FAKE_CODEX_THREAD_BUSY_ATTEMPTS': '0', 'FAKE_CODEX_RESUME_ERROR': '',
                'FAKE_CODEX_ROLLOUT': str(rollout)}

    def test_same_job_survives_three_resets_then_stops_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            rollout = root / 'rollout.jsonl'
            rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'existing', 'cwd': str(root)}}) + '\n')
            append_event(rollout, {'type': 'task_started', 'turn_id': 'original'})
            append_event(rollout, {'type': 'task_complete', 'turn_id': 'original',
                                  'error': {'codex_error_info': 'usage_limit_exceeded'}})
            job = runner.Job(root / 'state.json', {})
            job.save(status='watching', retry_at=None, thread_id='existing', watch_turn_id='original',
                     watch_rollout=str(rollout), cwd=str(root), model=None, workspace_write=False,
                     allow_network=False, prompt='继续剩余任务')
            statuses = []
            original = job.save
            def record(**changes):
                original(**changes)
                statuses.append(job.state['status'])
            with patch.dict(os.environ, self.environment(root, rollout)), patch.object(job, 'save', side_effect=record):
                asyncio.run(asyncio.wait_for(runner.run(job), timeout=15))
                counts = {name: int((root / 'calls' / name).read_text()) for name in ('turn_start', 'account_rateLimits_read')}
                asyncio.run(runner.run(job))
            self.assertEqual(job.state['status'], 'complete')
            self.assertGreaterEqual(statuses.count('waiting_quota'), 3)
            self.assertEqual(counts, {'turn_start': 3, 'account_rateLimits_read': 6})
            self.assertEqual(int((root / 'calls/turn_start').read_text()), 3)
            self.assertIsNone(job.state['watch_turn_id'])  # 自己的续跑轮次不误判成人工新任务。

    def test_hooks_rearm_next_user_turn_in_same_chat(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp); rollout = root / 'rollout.jsonl'
            rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'same-chat', 'cwd': str(root)}}) + '\n')
            env = {**os.environ, **self.environment(root, rollout)}
            env.pop('CODEX_AUTO_RESUME_CHILD', None)
            def submit(turn):
                event = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'same-chat', 'turn_id': turn,
                         'cwd': str(root), 'transcript_path': str(rollout)}
                subprocess.run([sys.executable, '-X', 'utf8', str(HOOK)], input=json.dumps(event),
                               env=env, text=True, capture_output=True, check=True)
                state = root / 'jobs/same-chat' / turn / 'state.json'
                wait_for(lambda: state.exists())
                return state
            append_event(rollout, {'type': 'task_started', 'turn_id': 'first'})
            first = submit('first')
            append_event(rollout, {'type': 'task_complete', 'turn_id': 'first'})
            wait_for(lambda: json.loads(first.read_text())['status'] == 'complete')
            self.assertFalse((root / 'calls/turn_start').exists())
            append_event(rollout, {'type': 'task_started', 'turn_id': 'second'})
            second = submit('second')
            self.assertNotEqual(first, second)
            append_event(rollout, {'type': 'task_complete', 'turn_id': 'second',
                                  'error': {'codex_error_info': 'usage_limit_exceeded'}})
            wait_for(lambda: json.loads(second.read_text())['status'] == 'complete')
            wait_for(lambda: not runner.runner_alive(second))
            self.assertEqual(int((root / 'calls/turn_start').read_text()), 3)
            self.assertEqual(json.loads(first.read_text())['status'], 'complete')
