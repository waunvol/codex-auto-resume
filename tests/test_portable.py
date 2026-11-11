import asyncio
import base64
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
import resume_platform as platform
import codex_auto_resume as runner
spec = importlib.util.spec_from_file_location('installer', REPO / 'install.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)
FAKE = Path(__file__).with_name('fake_codex.py')


def wait_for(predicate, seconds=15):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for expected state')


class PortableTests(unittest.TestCase):
    def test_install_upgrade_uninstall(self):
        with tempfile.TemporaryDirectory(prefix='中文 path ') as tmp:
            # install() 内部会 resolve()；Windows 的 8.3 短路径不 resolve 时与长路径不相等。
            home = Path(tmp).resolve()
            original = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo unrelated'}]}]},
                        'description': 'Keep this'}
            (home / 'hooks.json').write_text(json.dumps(original), encoding='utf-8')
            with patch.object(installer, 'resolve_codex', return_value=[sys.executable, str(FAKE)]):
                info = installer.install(home)
                first = (home / 'hooks.json').read_bytes()
                installer.install(home)
                self.assertEqual(first, (home / 'hooks.json').read_bytes())
                self.assertTrue((Path(info['runtime']) / 'codex_resume_hook.py').is_file())
                # 配置发布中断后，下一次安装仍能识别旧命令，不产生重复 hooks。
                real_write = installer.atomic_json
                def interrupted(path, data):
                    if path == home / 'hooks.json':
                        raise OSError('simulated interruption')
                    return real_write(path, data)
                with patch.object(installer, 'hook_command', return_value='new-command'), \
                     patch.object(installer, 'atomic_json', side_effect=interrupted):
                    with self.assertRaises(OSError):installer.install(home)
                installer.install(home)
                self.assertEqual(first, (home / 'hooks.json').read_bytes())
                installer.install(home, uninstall=True)
            restored = json.loads((home / 'hooks.json').read_text(encoding='utf-8'))
            self.assertEqual(restored['hooks']['Stop'], original['hooks']['Stop'])
            self.assertFalse(restored['hooks']['UserPromptSubmit'])
            self.assertEqual(restored['description'], original['description'])
            self.assertTrue(list((home / 'auto-resume').glob('hooks.backup-*')))

    def test_installed_runtime_survives_clone_removal(self):
        # 后台执行器释放锁与进程真正退出之间有窗口，Windows 上可能仍占用 runner.log。
        with tempfile.TemporaryDirectory(prefix='install 中文 ', ignore_cleanup_errors=True) as tmp:
            root = Path(tmp); clone = root/'clone'; home = root/'codex home'
            shutil.copytree(REPO/'src', clone)
            with patch.object(installer, 'SOURCE', clone), \
                 patch.object(installer, 'resolve_codex', return_value=[sys.executable, str(FAKE)]):
                info = installer.install(home)
            shutil.rmtree(clone)
            cwd = root/'project';cwd.mkdir();rollout = cwd/'rollout.jsonl'
            rows = [{'type':'session_meta','payload':{'id':'installed','cwd':str(cwd)}},
                    {'type':'event_msg','payload':{'type':'task_started','turn_id':'turn1'}}]
            rollout.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
            env = {**os.environ, 'CODEX_HOME':str(home), 'CODEX_RESUME_HOOK_ROOT':str(root/'jobs')}
            env.pop('CODEX_AUTO_RESUME_CHILD',None)
            event = {'hook_event_name':'UserPromptSubmit','session_id':'installed','turn_id':'turn1',
                     'cwd':str(cwd),'transcript_path':str(rollout)}
            subprocess.run([sys.executable,'-X','utf8',str(Path(info['runtime'])/'codex_resume_hook.py')],
                           input=json.dumps(event),text=True,encoding='utf-8',env=env,check=True,capture_output=True)
            state = root/'jobs/installed/turn1/state.json'
            wait_for(lambda:state.exists())
            with rollout.open('a',encoding='utf-8') as f:
                f.write(json.dumps({'type':'event_msg','payload':{'type':'task_complete','turn_id':'turn1'}})+'\n')
            wait_for(lambda:json.loads(state.read_text(encoding='utf-8'))['status']=='complete')
            wait_for(lambda:not runner.runner_alive(state))

    def test_windows_command_and_npm(self):
        command = installer.hook_command("C:\\A & B\\python.exe", "C:\\名字\\it's hook.py", windows=True)
        decoded = base64.b64decode(command.split()[-1]).decode('utf-16-le')
        self.assertIn("'C:\\名字\\it''s hook.py'", decoded)
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / 'codex.cmd';shim.touch()
            entry = Path(tmp) / 'node_modules/@openai/codex/bin/codex.js'
            entry.parent.mkdir(parents=True);entry.touch()
            with patch.object(platform.shutil, 'which', return_value='node.exe'):
                self.assertEqual(platform.resolve_codex(str(shim)), ['node.exe', str(entry)])

    def test_cross_process_lock_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / 'lock'
            code = ('import sys;sys.path.insert(0,sys.argv[1]);from resume_platform import lock_file;'
                    'f=open(sys.argv[2],"a+b");lock_file(f)')
            with lock.open('a+b') as stream:
                platform.lock_file(stream)
                result = subprocess.run([sys.executable, '-c', code, str(REPO/'src'), str(lock)],capture_output=True)
                self.assertNotEqual(result.returncode, 0)
            subprocess.run([sys.executable, '-c', code, str(REPO/'src'), str(lock)],check=True)

    def test_hooks_concurrent_and_recursion(self):
        with tempfile.TemporaryDirectory(prefix='中文 hooks ') as tmp:
            root = Path(tmp); cases = []
            for name in ('quota-a','quota-b','normal','manual'):
                cwd = root / name;cwd.mkdir();rollout = cwd/'rollout.jsonl'
                rows = [{'type':'session_meta','payload':{'id':name,'cwd':str(cwd)}},
                        {'type':'event_msg','payload':{'type':'task_started','turn_id':'turn1'}}]
                rollout.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
                event = {'hook_event_name':'UserPromptSubmit','session_id':name,'turn_id':'turn1',
                         'cwd':str(cwd),'transcript_path':str(rollout)}
                env = {**os.environ,'CODEX_HOME':str(root/'home'),'CODEX_RESUME_HOOK_ROOT':str(root/'jobs'),
                       'CODEX_RESUME_CODEX_COMMAND':json.dumps([sys.executable,str(FAKE)]),
                       'FAKE_CODEX_ROOT':str(cwd/'calls')}
                env.pop('CODEX_AUTO_RESUME_CHILD',None)
                def hook(e, environment=env):
                    return subprocess.run([sys.executable,'-X','utf8',str(REPO/'src/codex_resume_hook.py')],
                                          input=json.dumps(e),text=True,encoding='utf-8',env=environment,
                                          check=True,capture_output=True)
                hook(event);hook(event)
                state = root/'jobs'/name/'turn1/state.json'
                wait_for(lambda: state.exists())
                cases.append((name,rollout,state,event,env))
            for name,rollout,state,event,env in cases:
                payload = {'type':'task_complete','turn_id':'turn1'}
                if name.startswith('quota'):payload['error']={'codex_error_info':'usage_limit_exceeded'}
                if name=='manual':payload={'type':'task_started','turn_id':'turn2'}
                if name=='normal':hook({**event,'hook_event_name':'Stop'},env)
                with rollout.open('a',encoding='utf-8') as f:f.write(json.dumps({'type':'event_msg','payload':payload})+'\n')
            for name,rollout,state,event,env in cases:
                expected = 'cancelled' if name=='manual' else 'complete'
                wait_for(lambda: json.loads(state.read_text(encoding='utf-8'))['status']==expected)
                wait_for(lambda: not runner.runner_alive(state))
                count = root/name/'calls/turn_start'
                self.assertEqual(int(count.read_text()) if count.exists() else 0, int(name.startswith('quota')))
                hook({**event,'turn_id':'recursive'}, {**env,'CODEX_AUTO_RESUME_CHILD':'1'})
                self.assertFalse((root/'jobs'/name/'recursive').exists())

    @unittest.skipUnless(os.name=='nt', 'native Windows shell test')
    def test_windows_hook_shell(self):
        with tempfile.TemporaryDirectory(prefix='中文 & ') as tmp:
            script = Path(tmp)/"it's hook.py"
            script.write_text('import sys,json;print(json.dumps(json.load(sys.stdin)))',encoding='utf-8')
            command = installer.hook_command(sys.executable, script)
            result = subprocess.run(command.split(),input='{"value":"中文"}',text=True,encoding='utf-8',
                                    capture_output=True,check=True)
            self.assertEqual(json.loads(result.stdout),{'value':'中文'})


if __name__ == '__main__':
    unittest.main()
