"""项目级 DSH 宿主动作：不调用真实 Codex/DeepSeek。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import codex_auto_resume as runner


class DshProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = runner.Job(Path(self.temp.name) / 'supervisor.json', {
            'dsh_project':'demo', 'dsh_root':str(Path(self.temp.name) / 'jobs'),
            'status':'pending', 'summary':'old'})
        self.current = {'id':'demo','status':'needs_attention','running_count':0,
                        'cwd':self.temp.name,'tasks':[{'id':'code'}, {'id':'tests'}]}

    def action(self, kind, task='', text='实际证据已核查', resolved=None):
        return {'kind':kind,'task':task,'text':text,'resolved':resolved or []}

    def settle(self, status='continue', action=None):
        with patch.object(runner, 'dsh_command', return_value=self.current):
            runner.settle_project_turn(self.job, {'status':status,'summary':'new','dsh_action':action})

    def test_project_schema_does_not_change_single_job_schema(self):
        schema = runner.output_schema(self.job)
        properties = schema['properties']['dsh_action']['anyOf'][1]['properties']
        self.assertIn('finish', properties['kind']['enum'])
        self.assertIn('task', properties)
        single = runner.output_schema(runner.Job(self.job.path, {'dsh_job':'old'}))
        self.assertNotIn('task', single['properties']['dsh_action']['anyOf'][1]['properties'])
        self.assertEqual(runner.output_schema(runner.Job(self.job.path, {})), runner.RESULT_SCHEMA)

    def test_unfinished_project_cannot_complete(self):
        with self.assertRaises(runner.Blocked):
            self.settle('complete')
        with self.assertRaises(runner.Blocked):
            self.settle('complete', self.action('accepted', 'code'))

    def test_unknown_task_rejected_before_action_is_persisted(self):
        with self.assertRaises(runner.Blocked):
            self.settle(action=self.action('followup', 'unknown'))
        self.assertEqual(self.job.state['status'], 'pending')

    def test_assistant_followup_rejected_before_persisting_or_executing(self):
        action = self.action('followup', 'assistant')
        with self.assertRaisesRegex(runner.Blocked, 'assist'):
            self.settle(action=action)
        self.assertEqual(self.job.state['status'], 'pending')
        with patch.object(runner, 'dsh_command') as call:
            with self.assertRaisesRegex(runner.Blocked, 'assist'):
                runner.apply_project_action(self.job, {**action, 'id':'legacy-action'})
        call.assert_not_called()

    def test_assistant_review_and_finding_disposition(self):
        self.settle(action=self.action('accepted','assistant',resolved=['F1']))
        self.assertEqual(self.job.state['status'], 'dsh_action')
        action_id = self.job.state['dsh_action']['id']
        with patch.object(runner, 'dsh_command', return_value=self.current) as call:
            runner.apply_dsh_action(self.job)
        arguments = call.call_args.args
        self.assertIn('--resolve', arguments)
        self.assertIn('F1', arguments)
        self.assertIn(action_id, arguments)
        self.assertEqual(self.job.state['status'], 'pending')

    def test_running_project_waits_without_new_model_turn(self):
        self.current['running_count'] = 2
        self.settle('wait_dsh')
        self.assertEqual(self.job.state['status'], 'waiting_dsh')
        self.assertTrue(runner.dsh_running(self.job.state, self.current))

    def test_finish_requires_host_confirmation(self):
        self.settle('complete', self.action('finish'))
        with patch.object(runner, 'dsh_command', return_value=self.current):
            with self.assertRaises(runner.Blocked):
                runner.apply_dsh_action(self.job)
        self.current['status'] = 'complete'
        with patch.object(runner, 'dsh_command', return_value=self.current):
            runner.apply_dsh_action(self.job)
        self.assertEqual(self.job.state['status'], 'complete')

    def test_project_runner_uses_correct_id_and_root(self):
        entry = Path(self.temp.name) / 'dsh_project.py'
        entry.touch()
        result = type('Result', (), {'returncode':0,'stdout':json.dumps(self.current)})()
        with patch.object(runner, 'DSH_PROJECT_RUNNER', str(entry)), \
             patch.object(runner.subprocess, 'run', return_value=result) as call:
            runner.dsh_command(self.job, 'status')
        argv = call.call_args.args[0]
        self.assertEqual(argv[-2:], ['--id','demo'])
        self.assertEqual(call.call_args.kwargs['env']['DSH_COLLAB_ROOT'], self.job.state['dsh_root'])


if __name__ == '__main__':
    unittest.main()
