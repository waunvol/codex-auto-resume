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


if __name__ == '__main__':
    unittest.main()
