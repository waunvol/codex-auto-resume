import json
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
import resume_mcp as mcp
import resume_dashboard as dashboard
from resume_plugin import build_plugin
from resume_control import read_control,prompt_snapshot,write_control


class PluginTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node is only needed for the frontend regression test')
    def test_standard_handshake_with_legacy_host_api(self):
        result=subprocess.run([shutil.which('node'),str(REPO/'tests/test_bridge.cjs')],
                              capture_output=True,text=True,check=True)
        self.assertIn('PASS',result.stdout)

    def test_mcp_stdio_panel_and_control(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);task=root/'jobs/session/turn';task.mkdir(parents=True)
            (task/'state.json').write_text('{"status":"waiting_quota"}')
            env={**os.environ,'CODEX_RESUME_HOOK_ROOT':str(root/'jobs')}
            requests=[('initialize',{}),('tools/list',{}),('resources/read',{'uri':mcp.URI}),
                      ('tools/call',{'name':'open_resume_panel'}),
                      ('tools/call',{'name':'set_resume_control','arguments':{
                          'session':'session','turn':'turn','mode':'confirm','prompt':'new prompt','approve':True,'desktop_unlock':True}}),
                      ('tools/call',{'name':'set_resume_control','arguments':{
                          'session':'../outside','turn':'turn','mode':'auto'}})]
            lines=''.join(json.dumps({'jsonrpc':'2.0','id':i,'method':method,'params':params})+'\n'
                          for i,(method,params) in enumerate(requests))
            process=subprocess.run([sys.executable,'-X','utf8',str(REPO/'src/resume_mcp.py')],input=lines,
                                   capture_output=True,text=True,encoding='utf-8',env=env,check=True)
            responses=[json.loads(x) for x in process.stdout.splitlines()]
            self.assertEqual(len(responses),len(requests))
            self.assertEqual(responses[1]['result']['tools'][0]['_meta']['ui']['resourceUri'],mcp.URI)
            self.assertEqual(responses[1]['result']['tools'][0]['_meta']['openai/ui']['entrypoints'],
                             [{'type':'global'},{'type':'thread'}])
            self.assertEqual(responses[1]['result']['tools'][0]['title'],'恢复控制台')
            self.assertIn('ui/initialize',responses[2]['result']['contents'][0]['text'])
            self.assertEqual(len(responses[3]['result']['structuredContent']['jobs']),1)
            self.assertTrue(responses[4]['result']['structuredContent']['control']['approved'])
            self.assertTrue(responses[4]['result']['structuredContent']['control']['desktop_unlock'])
            self.assertTrue(responses[5]['result']['isError'])
            self.assertEqual(read_control(task/'state.json')['prompt'],'new prompt')

    def test_cli_prompt_file_is_read_late_and_changes_snapshot(self):
        with tempfile.TemporaryDirectory(prefix='prompt 中文 ') as d:
            root=Path(d);state=root/'state.json';state.write_text('{"status":"waiting_quota"}')
            prompt=root/'new prompt.md';prompt.write_text('第一版',encoding='utf-8-sig')
            subprocess.run([sys.executable,'-X','utf8',str(REPO/'src/resume_control.py'),
                            '--state',str(state),'--mode','confirm','--prompt-file',str(prompt),'--approve'],
                           check=True,capture_output=True)
            control=read_control(state);first=prompt_snapshot(control)
            self.assertEqual(first[0],'第一版')
            prompt.write_text('恢复前修改的新指令',encoding='utf-8')
            second=prompt_snapshot(control)
            self.assertEqual(second[0],'恢复前修改的新指令')
            self.assertNotEqual(first[1],second[1])
            prompt.unlink()
            with self.assertRaises(OSError):prompt_snapshot(control)

    def test_embedded_proxy_settings_persist_without_changing_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'home';prefs=home/'auto-resume/preferences.json'
            prefs.parent.mkdir(parents=True);prefs.write_text(json.dumps({'prompt_dir':'keep-prompts'}),encoding='utf-8')
            task=root/'jobs/session/turn';task.mkdir(parents=True)
            state=task/'state.json';state.write_text('{"status":"watching"}',encoding='utf-8');before=state.read_bytes()
            env={**os.environ,'CODEX_HOME':str(home),'CODEX_RESUME_HOOK_ROOT':str(root/'jobs')}
            env.pop('CODEX_RESUME_PROXY',None)
            requests=[('tools/list',{}),('tools/call',{'name':'list_resume_jobs'}),
                      *[('tools/call',{'name':'set_resume_proxy','arguments':{'proxy':value}})
                        for value in ('http://127.0.0.1:17890','direct','inherit','bad-url')],
                      ('tools/call',{'name':'set_resume_proxy','arguments':{'proxy':'direct','path':'outside'}}),
                      ('tools/call',{'name':'list_resume_jobs'})]
            lines=''.join(json.dumps({'jsonrpc':'2.0','id':i,'method':method,'params':params})+'\n'
                          for i,(method,params) in enumerate(requests))
            result=subprocess.run([sys.executable,'-X','utf8',str(REPO/'src/resume_mcp.py')],input=lines,
                                  capture_output=True,text=True,encoding='utf-8',env=env,check=True)
            replies=[json.loads(line)['result'] for line in result.stdout.splitlines()]
            tool=next(t for t in replies[0]['tools'] if t['name']=='set_resume_proxy')
            self.assertEqual(tool['_meta']['ui']['visibility'],['app'])
            self.assertFalse(tool['annotations']['readOnlyHint'])
            self.assertEqual(replies[1]['structuredContent']['network']['proxy'],'inherit')
            for i,value in enumerate(('http://127.0.0.1:17890','direct','inherit'),2):
                self.assertEqual(replies[i]['structuredContent']['network']['proxy'],value)
            self.assertTrue(replies[5]['isError']);self.assertTrue(replies[6]['isError'])
            self.assertEqual(replies[7]['structuredContent']['network']['proxy'],'inherit')
            self.assertEqual(json.loads(prefs.read_text(encoding='utf-8')),{'prompt_dir':'keep-prompts','proxy':'inherit'})
            self.assertEqual(state.read_bytes(),before)
            self.assertFalse((task/'control.json').exists())

    def test_proxy_error_does_not_hide_tasks_and_environment_override_is_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prefs=root/'auto-resume/preferences.json';prefs.parent.mkdir()
            prefs.write_text('{"proxy":"bad-url","prompt_dir":"keep"}',encoding='utf-8')
            with patch.dict(os.environ,{'CODEX_HOME':temp,'CODEX_RESUME_PROXY':'direct'}), \
                    patch.object(mcp,'jobs',return_value=[{'status':'watching'}]):
                data=mcp.dispatch('tools/call',{'name':'list_resume_jobs'})['structuredContent']
                self.assertEqual(len(data['jobs']),1);self.assertIn('error',data['network'])
                data=mcp.dispatch('tools/call',{'name':'set_resume_proxy','arguments':{'proxy':'http://localhost:7890'}})
                self.assertTrue(data['structuredContent']['network']['environment_override'])
                self.assertEqual(json.loads(prefs.read_text(encoding='utf-8'))['prompt_dir'],'keep')

    def test_generated_plugin_is_self_contained(self):
        with tempfile.TemporaryDirectory() as d:
            runtime=Path(d)/'home/auto-resume/versions/hash';runtime.mkdir(parents=True)
            build_plugin(runtime)
            catalog=json.loads((runtime/'.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
            source=runtime/catalog['plugins'][0]['source']['path']
            self.assertTrue((source/'.codex-plugin/plugin.json').is_file())
            config=json.loads((source/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']['resume']
            # Windows TEMP 可能包含 RUNNER~1；生成器 resolve() 后采用长路径。比较位置而非别名字符串。
            self.assertEqual(Path(config['args'][-1]).resolve(), (runtime/'resume_mcp.py').resolve())
            self.assertEqual(Path(config['env']['CODEX_HOME']).resolve(), runtime.resolve().parents[2])


if __name__=='__main__':unittest.main()
