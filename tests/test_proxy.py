import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
import resume_platform as platform
import codex_auto_resume as runner
spec = importlib.util.spec_from_file_location('proxy_installer', REPO / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
FAKE = Path(__file__).with_name('fake_codex.py')
PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')


class ProxyTests(unittest.TestCase):
    def test_modes_environment_override_and_parent_environment_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            installer.save_preferences(home, {'proxy': 'http://127.0.0.1:17890'})
            inherited = {key: 'http://old:9999' for key in PROXY_KEYS}
            with patch.dict(os.environ, {**inherited, 'CODEX_HOME': str(home),
                                        'NO_PROXY': 'localhost,custom', 'OTHER_SETTING': 'keep'}, clear=True):
                env = platform.codex_environment()
                self.assertEqual(env['HTTP_PROXY'], 'http://127.0.0.1:17890')
                self.assertEqual(env['https_proxy'], env['HTTP_PROXY'])
                self.assertNotIn('ALL_PROXY', env)
                self.assertEqual(env['no_proxy'], 'localhost,custom')
                self.assertEqual(env['OTHER_SETTING'], 'keep')
                self.assertEqual(os.environ['HTTP_PROXY'], inherited['HTTP_PROXY'])
                os.environ['CODEX_RESUME_PROXY'] = 'inherit'
                self.assertEqual(platform.codex_environment(), dict(os.environ))
                os.environ['CODEX_RESUME_PROXY'] = 'direct'
                self.assertFalse(set(PROXY_KEYS) & platform.codex_environment().keys())
                os.environ['CODEX_RESUME_PROXY'] = 'http://127.0.0.1:27890'
                self.assertEqual(platform.codex_environment()['HTTPS_PROXY'], os.environ['CODEX_RESUME_PROXY'])

    def test_missing_configuration_inherits_and_explicit_proxy_bypasses_localhost(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'CODEX_HOME': temp, 'HTTPS_PROXY': 'http://inherited:7890'}, clear=True):
                self.assertEqual(platform.codex_environment(), dict(os.environ))
                installer.save_preferences(Path(temp), {'proxy': 'http://[::1]:7890'})
                self.assertEqual(platform.codex_environment()['NO_PROXY'], 'localhost,127.0.0.1,::1')

    def test_invalid_proxy_configuration_is_reported(self):
        for value in ('', 7890, None, '127.0.0.1:7890', 'socks5://localhost:7890',
                      'http://localhost:bad', 'http://localhost:0', 'http://localhost:65536',
                      'http://bad host:7890', 'http://localhost:7890/path', 'http://localhost:7890?x=1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform.validate_proxy(value)
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            installer.save_preferences(home, {'proxy': 'direct'})
            path = home / 'auto-resume/preferences.json'
            for text in ('{invalid', '[]', '{"proxy": null}'):
                path.write_text(text, encoding='utf-8')
                with patch.dict(os.environ, {'CODEX_HOME': temp}, clear=True), self.assertRaises(ValueError):
                    platform.codex_environment()

    def test_configure_only_and_install_preserve_preferences_and_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            installer.save_preferences(home, {'prompt_dir': 'existing-prompts', 'custom': 'keep'})
            hooks = home / 'hooks.json'
            hooks.write_text('{"hooks": {}}', encoding='utf-8')
            original = hooks.read_bytes()
            argv = ['install.py', '--codex-home', temp, '--configure-proxy', 'http://localhost:17890']
            with patch.object(sys, 'argv', argv), patch.object(installer, 'install') as install, redirect_stdout(io.StringIO()):
                self.assertEqual(installer.main(), 0)
                install.assert_not_called()
            self.assertEqual(hooks.read_bytes(), original)
            path = home / 'auto-resume/preferences.json'
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['prompt_dir'], 'existing-prompts')
            argv = ['install.py', '--codex-home', temp, '--no-dashboard', '--prompt-dir', str(home / 'new-prompts')]
            with patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()), \
                    patch.object(installer, 'resolve_codex', return_value=[sys.executable, str(FAKE)]):
                self.assertEqual(installer.main(), 0)
                installer.install(home)
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['proxy'], 'http://localhost:17890')
            self.assertEqual(saved['custom'], 'keep')
            self.assertEqual(saved['prompt_dir'], str((home / 'new-prompts').resolve()))

    def test_next_connection_reads_changed_proxy_and_child_receives_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wrapper = root / 'capture.py'
            capture = root / 'capture.jsonl'
            wrapper.write_text(
                'import os,json,runpy\nfrom pathlib import Path\n'
                f'with Path({str(capture)!r}).open("a",encoding="utf-8") as f:\n'
                ' f.write(json.dumps({k:os.environ.get(k) for k in '
                '("HTTPS_PROXY","https_proxy","ALL_PROXY","CODEX_AUTO_RESUME_CHILD")})+"\\n")\n'
                f'runpy.run_path({str(FAKE)!r},run_name="__main__")\n', encoding='utf-8')
            job = runner.Job(root / 'state.json', {})
            job.save(status='pending', thread_id='existing', cwd=str(root.resolve()), model=None,
                     workspace_write=False, allow_network=False, prompt='fixture')
            with patch.dict(os.environ, {'CODEX_HOME': str(root / 'home'), 'FAKE_CODEX_ROOT': str(root / 'calls'),
                                        'CODEX_RESUME_CODEX_COMMAND': json.dumps([sys.executable, str(wrapper)]),
                                        'HTTPS_PROXY': 'http://stale:1234'}):
                os.environ.pop('CODEX_RESUME_PROXY', None)
                installer.save_preferences(root / 'home', {'proxy': 'http://localhost:17890'})
                asyncio.run(runner.drive_once(job))
                self.assertEqual(job.state['status'], 'waiting_quota')
                installer.save_preferences(root / 'home', {'proxy': 'http://localhost:27890'})
                asyncio.run(runner.drive_once(job))
            self.assertEqual(job.state['status'], 'complete')
            rows = [json.loads(line) for line in capture.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([row['HTTPS_PROXY'] for row in rows],
                             ['http://localhost:17890', 'http://localhost:27890'])
            self.assertTrue(all(row['HTTPS_PROXY'] == row['https_proxy']
                                and row['ALL_PROXY'] is None and row['CODEX_AUTO_RESUME_CHILD'] == '1'
                                for row in rows))
            self.assertEqual((root / 'calls/turn_start').read_text(), '1')

    def test_invalid_config_blocks_before_starting_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = runner.Job(root / 'state.json', {})
            job.save(status='pending', cwd=str(root))
            with patch.dict(os.environ, {'CODEX_RESUME_PROXY': 'bad-url'}), \
                    patch.object(runner.asyncio, 'create_subprocess_exec') as start:
                asyncio.run(runner.run(job))
            start.assert_not_called()
            self.assertEqual(job.state['status'], 'blocked')
            self.assertIn('代理配置', job.state['summary'])


if __name__ == '__main__':
    unittest.main()
