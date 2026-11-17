"""真实故障回放：额度已恢复，但桌面仍持有原聊天写锁。"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import codex_auto_resume as runner
import resume_dashboard as dashboard
from resume_control import read_control

FAKE = Path(__file__).with_name('fake_codex.py')


class ThreadBusyTests(unittest.TestCase):
    def setup_job(self, root):
        job = runner.Job(root / 'state.json', {})
        job.save(status='waiting_quota', retry_at=time.time() - 1, thread_id='existing',
                 cwd=str(root.resolve()), model=None, workspace_write=False, allow_network=False,
                 prompt='继续未完成任务')
        return job

    def environment(self, root):
        return patch.dict(os.environ, {
            'CODEX_HOME':str(root / 'home'), 'FAKE_CODEX_ROOT':str(root / 'calls'),
            'CODEX_RESUME_CODEX_COMMAND':json.dumps([sys.executable, str(FAKE)]),
            'FAKE_CODEX_THREAD_BUSY_ATTEMPTS':'2', 'FAKE_CODEX_RESUME_ERROR':'',
        })

    def test_busy_retries_after_reset_and_submits_exactly_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = self.setup_job(root)
            states = []
            original = job.save
            def save(**changes):
                original(**changes)
                states.append(job.state['status'])
            with self.environment(root), patch.object(runner, 'RECHECK_SECONDS', .01), patch.object(job, 'save', side_effect=save):
                asyncio.run(asyncio.wait_for(runner.run(job), timeout=15))
            self.assertIn('waiting_thread', states)
            self.assertEqual(job.state['status'], 'complete')
            self.assertEqual((root / 'calls/thread_resume').read_text(), '3')
            self.assertEqual((root / 'calls/turn_start').read_text(), '1')

    def test_other_resume_error_is_visible_blocked_not_dead_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = self.setup_job(root)
            with self.environment(root), patch.dict(os.environ, {'FAKE_CODEX_THREAD_BUSY_ATTEMPTS':'0','FAKE_CODEX_RESUME_ERROR':'model not supported'}):
                asyncio.run(asyncio.wait_for(runner.run(job), timeout=15))
            self.assertEqual(job.state['status'], 'blocked')
            self.assertIn('model not supported', job.state['summary'])
            self.assertFalse((root / 'calls/turn_start').exists())

    def test_network_failure_while_waiting_for_writer_retries_then_resumes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = self.setup_job(root)
            states = []
            original = job.save
            def save(**changes):
                original(**changes)
                states.append(job.state['status'])
            with self.environment(root), patch.dict(os.environ, {'FAKE_CODEX_QUOTA_ERROR_AT': '3'}), \
                    patch.object(runner, 'RECHECK_SECONDS', .01), patch.object(job, 'save', side_effect=save):
                asyncio.run(asyncio.wait_for(runner.run(job), timeout=15))
            self.assertNotIn('blocked', states)
            self.assertEqual(job.state['status'], 'complete')
            self.assertEqual((root / 'calls/thread_resume').read_text(), '3')
            self.assertEqual((root / 'calls/turn_start').read_text(), '1')

    def test_active_thread_waits_for_idle_without_starting_duplicate_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = self.setup_job(root)
            with self.environment(root), patch.dict(os.environ, {
                    'FAKE_CODEX_THREAD_ACTIVE_READS': '1', 'FAKE_CODEX_THREAD_BUSY_ATTEMPTS': '0'}), \
                    patch.object(runner, 'RECHECK_SECONDS', .01):
                asyncio.run(asyncio.wait_for(runner.run(job), timeout=15))
            self.assertEqual(job.state['status'], 'complete')
            self.assertEqual((root / 'calls/thread_read').read_text(), '2')
            self.assertEqual((root / 'calls/thread_resume').read_text(), '1')
            self.assertEqual((root / 'calls/turn_start').read_text(), '1')

    def test_quota_transport_timeout_retries_but_authentication_error_blocks(self):
        class QuotaFailure:
            def __init__(self, text):self.text = text
            async def request(self, method, params):
                if method == 'initialize':return {}
                if method == 'account/read':return {'account': {'type': 'chatgpt'}}
                raise runner.RunnerError(self.text)
            async def send(self, message):pass
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for state in ('waiting_quota', 'waiting_thread'):
                for error in ('account/rateLimits/read 超时', 'account/rateLimits/read: please sign in again'):
                    with self.subTest(state=state, error=error):
                        job = self.setup_job(root)
                        job.save(status=state)
                        before = time.time()
                        if '超时' in error:
                            asyncio.run(runner.drive(QuotaFailure(error), job))
                            self.assertEqual(job.state['status'], state)
                            self.assertGreaterEqual(job.state['retry_at'], before + 30)
                        else:
                            with self.assertRaises(runner.RunnerError):
                                asyncio.run(runner.drive(QuotaFailure(error), job))

    def test_manual_new_turn_cancels_writer_wait_without_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rollout = root / 'rollout.jsonl'
            rows = [{'type':'session_meta','payload':{'id':'existing','cwd':str(root)}},
                    {'type':'event_msg','payload':{'type':'task_started','turn_id':'new-turn'}}]
            rollout.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            job = self.setup_job(root)
            job.save(status='waiting_thread', watch_turn_id='old-turn', watch_rollout=str(rollout))
            with patch.object(runner, 'drive_once') as drive:
                asyncio.run(runner.run(job))
            drive.assert_not_called()
            self.assertEqual(job.state['status'], 'cancelled')

    def test_writer_wait_remains_editable_in_dashboard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'session/turn'
            folder.mkdir(parents=True)
            state = folder / 'state.json'
            state.write_text(json.dumps({'status':'waiting_thread'}))
            with patch.object(dashboard, 'state_root', return_value=root):
                dashboard.apply_control({'session':'session','turn':'turn','mode':'off','prompt':'先暂停'})
            self.assertEqual(read_control(state)['mode'], 'off')

    def test_app_tool_followup_supersedes_old_watch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); rollout = root / 'rollout.jsonl'
            rows = [{'type': 'session_meta', 'payload': {'id': 'existing', 'cwd': str(root)}},
                    {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'original',
                     'error': {'codex_error_info': 'usage_limit_exceeded'}}},
                    {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new-turn',
                     'root_turn_id': 'sender-turn', 'turn_attribution': {'turn_trigger': 'app_tool_send_message'}}}]
            rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            job = self.setup_job(root)
            job.save(status='waiting_thread', watch_rollout=str(rollout), watch_turn_id='original')
            self.assertFalse(runner.check_watched_turn(job))
            self.assertEqual(job.state['status'], 'cancelled')

    def test_app_tool_followup_completion_is_tracked_but_subagent_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); rollout = root / 'rollout.jsonl'
            rows = [{'type': 'session_meta', 'payload': {'id': 'existing', 'cwd': str(root)}},
                    {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new-turn',
                     'root_turn_id': 'sender-turn', 'turn_attribution': {'turn_trigger': 'app_tool_send_message'}}},
                    {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'child',
                     'root_turn_id': 'sender-turn', 'turn_attribution': {'turn_trigger': 'subagent'}}},
                    {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'child',
                     'root_turn_id': 'sender-turn'}}]
            rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            job = self.setup_job(root)
            job.save(status='watching', watch_rollout=str(rollout), watch_turn_id='new-turn')
            self.assertFalse(runner.check_watched_turn(job))
            self.assertEqual(job.state['status'], 'watching')
            with rollout.open('a') as stream:
                stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete',
                    'turn_id': 'new-turn', 'root_turn_id': 'sender-turn'}}) + '\n')
            self.assertFalse(runner.check_watched_turn(job))
            self.assertEqual(job.state['status'], 'complete')


if __name__ == '__main__':
    unittest.main()
