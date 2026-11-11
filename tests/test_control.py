import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
import resume_dashboard as dashboard
import resume_platform as platform
import codex_auto_resume as runner
from resume_control import permitted,write_control,read_control


class ControlTests(unittest.TestCase):
    def test_paused_job_still_recovers_already_submitted_turn(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state.json';job=runner.Job(state,{})
            job.save(status='running',thread_id='thread',turn_id='submitted',applied_control_id='old')
            write_control(state,'off','',False)
            calls=[]
            async def recover_only(current):
                calls.append(current.state['turn_id'])
                current.save(status='complete')
            with patch.object(runner,'drive_once',side_effect=recover_only):
                asyncio.run(asyncio.wait_for(runner.run(job),timeout=1))
            self.assertEqual(calls,['submitted'])
            self.assertEqual(job.state['status'],'complete')

    def test_desktop_only_discovery(self):
        with tempfile.TemporaryDirectory() as d:
            exe=Path(d)/'codex.exe';exe.touch()
            with patch.object(platform.shutil,'which',return_value=None),patch.object(platform,'desktop_candidates',return_value=[exe]):
                self.assertEqual(platform.resolve_codex(runtime='desktop'),[str(exe)])
                self.assertEqual(platform.resolve_codex(),[str(exe)])
                with self.assertRaises(ValueError):platform.resolve_codex(runtime='cli')

    def test_pause_then_resume_with_prompt_once(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root=Path(d);state=root/'state.json'
            job=runner.Job(state,{});job.save(status='pending',cwd=str(root),prompt='original',model=None,
                                            workspace_write=False,allow_network=False,thread_id=None)
            write_control(state,'confirm','追加：先检查已有测试',False)
            env={**os.environ,'CODEX_HOME':str(root/'home'),'FAKE_CODEX_ROOT':str(root/'calls'),
                 'CODEX_RESUME_CODEX_COMMAND':json.dumps([sys.executable,str(REPO/'tests/fake_codex.py')])}
            with (root/'log').open('wb') as log:
                process=subprocess.Popen([sys.executable,'-X','utf8',str(REPO/'src/codex_auto_resume.py'),'run','--state',str(state)],env=env,stdout=log,stderr=log)
                try:
                    time.sleep(.5)
                    self.assertFalse((root/'calls').exists(),'pause must not query model/account')
                    control=write_control(state,'confirm','追加：先检查已有测试',True)
                    process.wait(timeout=15)
                    self.assertEqual(process.returncode,0,(root/'log').read_text(encoding='utf-8'))
                    data=json.loads((root/'calls/last_prompt.json').read_text(encoding='utf-8'))
                    self.assertIn('追加：先检查已有测试',data['input'][0]['text'])
                    saved=json.loads(state.read_text(encoding='utf-8'))
                    self.assertEqual(saved['applied_control_id'],control['revision'])
                    self.assertFalse(permitted(state,control['revision']))
                    self.assertEqual((root/'calls/turn_start').read_text(),'1')
                finally:
                    if process.poll() is None:process.kill();process.wait()

    def test_dashboard_auth_and_task_isolation(self):
        with tempfile.TemporaryDirectory() as d,patch.object(dashboard,'state_root',return_value=Path(d)):
            for name in ('one','two'):
                p=Path(d)/name/'turn';p.mkdir(parents=True)
                (p/'state.json').write_text(json.dumps({'status':'waiting_quota','cwd':'project'}),encoding='utf-8')
            server=dashboard.ThreadingHTTPServer(('127.0.0.1',0),dashboard.Handler);server.token=secrets.token_urlsafe()
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def request(path,body=None,auth=True,extra=None):
                headers={'Authorization':'Bearer '+server.token} if auth else {}
                headers.update(extra or {})
                return urlopen(Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers),timeout=2)
            try:
                with self.assertRaises(HTTPError) as e:request('/api/jobs',auth=False)
                self.assertEqual(e.exception.code,403)
                with self.assertRaises(HTTPError):request('/api/jobs',extra={'Origin':'https://evil.example'})
                with request('/api/jobs') as r:self.assertEqual(len(json.load(r)['jobs']),2)
                with request('/api/control',{'session':'one','turn':'turn','mode':'confirm','prompt':'new instruction','approve':True}) as r:
                    self.assertTrue(json.load(r)['control']['approved'])
                self.assertEqual(read_control(Path(d)/'two/turn/state.json')['prompt'],'')
                with self.assertRaises(HTTPError):request('/api/control',{'session':'..','turn':'turn','mode':'auto'})
                p=Path(d)/'one/turn/state.json';p.write_text('{"status":"complete"}')
                with self.assertRaises(HTTPError):request('/api/control',{'session':'one','turn':'turn','mode':'auto'})
                with request('/') as r:self.assertIn('textarea',r.read().decode())
            finally:
                server.shutdown();server.server_close();thread.join()
