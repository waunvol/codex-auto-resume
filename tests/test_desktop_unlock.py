import asyncio
import json
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import codex_auto_resume as runner
from resume_control import read_control,write_control
from resume_desktop import DesktopBridge,DesktopError,ThreadChanged,RestoreUnconfirmed,release_writer


class DesktopUnlockTests(unittest.TestCase):
    def bridge(self,status='idle',turn='original',fail_restore=False,fail_archive=False):
        bridge=DesktopBridge('thread','original',endpoint='unused')
        calls=[]
        def call(tool,args):
            if tool=='read_thread':
                data={'thread':{'id':'thread','status':{'type':status}},
                      'turns':[{'id':turn,'status':'completed'}]}
                return {'contentItems':[{'type':'inputText','text':json.dumps(data)}]}
            calls.append(args['archived'])
            if (not args['archived'] and fail_restore) or (args['archived'] and fail_archive):
                raise DesktopError('transport error')
            return {'success':True}
        bridge.call=call
        bridge.ensure_no_native_children=lambda:None
        return bridge,calls

    def test_idle_exact_round_archives_and_restores_same_thread(self):
        bridge,calls=self.bridge()
        result=release_writer('thread','original',bridge)
        self.assertEqual(calls,[True,False]);self.assertTrue(result['released']);self.assertTrue(result['restored'])

    def test_active_or_new_round_is_never_archived(self):
        for status,turn in [('active','original'),('idle','new-turn')]:
            bridge,calls=self.bridge(status,turn)
            with self.assertRaises(ThreadChanged):release_writer('thread','original',bridge)
            self.assertEqual(calls,[])
        bridge,calls=self.bridge('notLoaded')
        with self.assertRaises(DesktopError):release_writer('thread','original',bridge)
        self.assertEqual(calls,[])

    def test_ambiguous_archive_failure_still_attempts_restore(self):
        bridge,calls=self.bridge(fail_archive=True)
        with self.assertRaisesRegex(DesktopError,'transport'):release_writer('thread','original',bridge)
        self.assertEqual(calls,[True,False])
        bridge,calls=self.bridge(fail_restore=True)
        with self.assertRaises(RestoreUnconfirmed):release_writer('thread','original',bridge)
        self.assertEqual(calls,[True,False])

    def test_consent_is_explicit_preserved_and_revocable(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'state.json';path.write_text('{"status":"waiting_thread"}')
            self.assertFalse(read_control(path)['desktop_unlock'])
            write_control(path,'auto','',desktop_unlock=True)
            write_control(path,'confirm','new instructions')
            self.assertTrue(read_control(path)['desktop_unlock'])
            write_control(path,'confirm','',desktop_unlock=False)
            self.assertFalse(read_control(path)['desktop_unlock'])
            with self.assertRaises(ValueError):write_control(path,'auto','',desktop_unlock='true')

    def test_native_child_relationship_blocks_recursive_archive(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CODEX_HOME':temp}):
            db=sqlite3.connect(Path(temp)/'state_5.sqlite')
            db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
            db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)',('thread','child'));db.commit();db.close()
            bridge=DesktopBridge('thread','original',endpoint='unused')
            with self.assertRaisesRegex(DesktopError,'原生 Codex 子会话'):bridge.ensure_no_native_children()
            DesktopBridge('unrelated','original',endpoint='unused').ensure_no_native_children()

    def test_interrupted_unlock_restores_checkpoint_before_reading_rollout(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'state.json';job=runner.Job(path,{})
            job.save(status='waiting_thread',thread_id='thread',watch_turn_id='original',
                     watch_rollout=str(Path(temp)/'currently-moved-rollout'))
            with patch.object(runner,'desktop_request',side_effect=runner.Blocked('uncertain transport')):
                with self.assertRaises(runner.Blocked):asyncio.run(runner.release_desktop_writer(job,'original'))
            self.assertEqual(job.state['desktop_unlock_pending']['turn_id'],'original')
            job.save(status='blocked')
            with patch.object(runner,'desktop_request',return_value={'restored':True}) as request, \
                    patch.object(runner,'check_watched_turn') as watch,patch.object(runner,'drive_once') as model:
                asyncio.run(runner.run(job))
            self.assertTrue(request.call_args.args[1]['restore_only']);watch.assert_not_called();model.assert_not_called()
            self.assertIsNone(job.state['desktop_unlock_pending'])

    def test_restore_failure_stops_with_checkpoint_instead_of_retrying_forever(self):
        with tempfile.TemporaryDirectory() as temp:
            job=runner.Job(Path(temp)/'state.json',{})
            job.save(status='waiting_thread',desktop_unlock_pending={'thread_id':'thread','turn_id':'original'})
            with patch.object(runner,'desktop_request',side_effect=runner.Blocked('restore unavailable')) as request:
                asyncio.run(asyncio.wait_for(runner.run(job),1))
            self.assertEqual(request.call_count,1);self.assertEqual(job.state['status'],'blocked')
            self.assertIsNotNone(job.state['desktop_unlock_pending'])

    def test_writer_conflict_uses_opted_in_release_then_resumes_exactly_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();rollout=root/'rollout.jsonl'
            rows=[{'type':'session_meta','payload':{'id':'existing','cwd':str(root)}},
                  {'type':'event_msg','payload':{'type':'task_complete','turn_id':'original',
                   'error':{'codex_error_info':'usage_limit_exceeded'}}}]
            rollout.write_text(''.join(json.dumps(x)+'\n' for x in rows))
            job=runner.Job(root/'state.json',{})
            job.save(status='waiting_quota',thread_id='existing',cwd=str(root),model=None,
                     workspace_write=False,allow_network=False,prompt='fixture',
                     watch_turn_id='original',watch_rollout=str(rollout))
            write_control(job.path,'auto','',desktop_unlock=True)
            fake=Path(__file__).with_name('fake_codex.py')
            env={'CODEX_HOME':str(root/'home'),'FAKE_CODEX_ROOT':str(root/'calls'),
                 'CODEX_RESUME_CODEX_COMMAND':json.dumps([sys.executable,str(fake)]),
                 'FAKE_CODEX_THREAD_BUSY_ATTEMPTS':'1','FAKE_CODEX_ROLLOUT':str(rollout)}
            with patch.dict(os.environ,env),patch.object(runner,'release_desktop_writer',return_value=None) as release:
                asyncio.run(runner.drive_once(job));asyncio.run(runner.drive_once(job))
            release.assert_called_once_with(job,'original')
            self.assertEqual(job.state['status'],'complete')
            self.assertEqual((root/'calls/thread_resume').read_text(),'2')
            self.assertEqual((root/'calls/turn_start').read_text(),'1')


if __name__=='__main__':unittest.main()
