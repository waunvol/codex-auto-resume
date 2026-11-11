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
                          'session':'session','turn':'turn','mode':'confirm','prompt':'new prompt','approve':True}}),
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

    def test_generated_plugin_is_self_contained(self):
        with tempfile.TemporaryDirectory() as d:
            runtime=Path(d)/'home/auto-resume/versions/hash';runtime.mkdir(parents=True)
            build_plugin(runtime)
            catalog=json.loads((runtime/'.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
            source=runtime/catalog['plugins'][0]['source']['path']
            self.assertTrue((source/'.codex-plugin/plugin.json').is_file())
            config=json.loads((source/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']['resume']
            self.assertEqual(config['args'][-1],str(runtime/'resume_mcp.py'))
            self.assertEqual(config['env']['CODEX_HOME'],str(runtime.parents[2]))


if __name__=='__main__':unittest.main()
