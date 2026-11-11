import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import codex_auto_resume as runner
import resume_inbox as inbox
from resume_control import read_control,prompt_snapshot


class InboxTests(unittest.TestCase):
    def test_names_collision_consume_and_preserve_late_edits(self):
        with tempfile.TemporaryDirectory() as d,patch.object(inbox,'directory',return_value=Path(d)/'prompts'):
            root=Path(d)
            def job(name):
                folder=root/name;folder.mkdir();j=runner.Job(folder/'state.json',{})
                j.save(thread_id=name,status='pending');inbox.create_prompt(j,'同名任务/标题');return j
            first,second=job('one'),job('two')
            a,b=Path(first.state['inbox_file']),Path(second.state['inbox_file'])
            self.assertNotEqual(a,b);self.assertEqual(a.name,'同名任务_标题.md')
            a.write_text('恢复追加指令',encoding='utf-8')
            control=read_control(first.path);text,_=prompt_snapshot(control)
            first.save(inbox_submission=inbox.submitted_snapshot(first.state,control,text))
            runner.settle_turn(first,{'status':'failed','error':{'codexErrorInfo':'usageLimitExceeded'}},None)
            self.assertTrue(a.exists(),'quota rejection must retain instructions')
            runner.settle_turn(first,{'status':'completed'},'{"status":"complete","summary":"done"}')
            self.assertFalse(a.exists());self.assertIsNone(read_control(first.path)['prompt_file'])
            b.write_text('提交时版本',encoding='utf-8');control=read_control(second.path)
            second.save(inbox_submission=inbox.submitted_snapshot(second.state,control,'提交时版本'))
            b.write_text('用户在提交后改写了新指令',encoding='utf-8')
            inbox.clean_prompt(second,consumed=True)
            self.assertEqual(b.read_text(encoding='utf-8'),'用户在提交后改写了新指令')

    def test_empty_finished_task_cleanup_and_title_database(self):
        with tempfile.TemporaryDirectory() as d,patch.object(inbox,'directory',return_value=Path(d)/'prompts'),patch.object(inbox,'codex_home',return_value=Path(d)):
            root=Path(d);job=runner.Job(root/'state.json',{});job.save(thread_id='thread',status='watching')
            with closing(sqlite3.connect(root/'state_5.sqlite')) as db:
                db.execute('CREATE TABLE threads(id TEXT,title TEXT)');db.execute('INSERT INTO threads VALUES (?,?)',('thread','真实任务标题'));db.commit()
            self.assertEqual(inbox.task_title({'session_id':'thread','prompt':'fallback'}),'真实任务标题')
            inbox.create_prompt(job,'CON');file=Path(job.state['inbox_file'])
            self.assertTrue(file.name.startswith('任务_CON'))
            inbox.clean_prompt(job);self.assertFalse(file.exists())


if __name__=='__main__':unittest.main()
