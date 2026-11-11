"""生成可由 Codex 官方本地 marketplace 安装的插件，无需服务器端口。"""
import json
import os
from pathlib import Path
import subprocess
import sys
from resume_platform import quiet_options

NAME='codex-auto-resume'
MARKET='codex-auto-resume-local'


def build_plugin(runtime, root=None):
    runtime=Path(runtime).resolve(); root=Path(root or runtime).resolve(); plugin=root/'plugin'
    (plugin/'.codex-plugin').mkdir(parents=True,exist_ok=True)
    catalog=root/'.agents/plugins';catalog.mkdir(parents=True,exist_ok=True)
    def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save(plugin/'.codex-plugin/plugin.json',{'name':NAME,'version':'0.3.0',
         'description':'本机 Codex 额度恢复控制台：状态、确认恢复、追加 prompt。',
         'mcpServers':'./.mcp.json','interface':{'displayName':'Codex 恢复控制台',
         'shortDescription':'查看任务并控制额度恢复后的执行','capabilities':['Interactive','Read','Write']}})
    save(plugin/'.mcp.json',{'mcpServers':{'resume':{'command':sys.executable,
         'args':['-X','utf8',str(runtime/'resume_mcp.py')],
         'env':{'CODEX_HOME':str(runtime.parents[2])},
         'env_vars':['CODEX_RESUME_HOOK_ROOT']}}})
    save(catalog/'marketplace.json',{'name':MARKET,'interface':{'displayName':'Codex Auto Resume'},
         'plugins':[{'name':NAME,'source':{'source':'local','path':'./plugin'},
                     'policy':{'installation':'AVAILABLE','authentication':'ON_USE'},'category':'Productivity'}]})
    return root


def install_plugin(runtime,cli,home):
    root=build_plugin(runtime, Path(home)/'auto-resume/plugin-marketplace')
    env={**os.environ,'CODEX_HOME':str(Path(home).resolve())}
    for args in (['plugin','marketplace','add',str(root),'--json'],['plugin','add',NAME+'@'+MARKET,'--json']):
        result=subprocess.run([*cli,*args],env=env,capture_output=True,text=True,encoding='utf-8',
                              errors='replace',timeout=60,**quiet_options())
        if result.returncode:raise RuntimeError('插件安装失败：'+(result.stderr or result.stdout).strip())
    return NAME+'@'+MARKET
