#!/usr/bin/env python3
"""本地 stdio MCP Apps：不监听端口，复用任务控制与 UI。"""
import json
import os
from pathlib import Path
import sys
from resume_dashboard import jobs, apply_control
from resume_platform import codex_home, save_preferences, validate_proxy

URI='ui://codex-auto-resume/panel.html'
EMPTY={'type':'object','properties':{},'additionalProperties':False}
CONTROL={'type':'object','properties':{'session':{'type':'string'},'turn':{'type':'string'},
         'mode':{'type':'string','enum':['auto','confirm','off']},'prompt':{'type':'string','maxLength':20000},
         'approve':{'type':'boolean'},'desktop_unlock':{'type':'boolean'}},
         'required':['session','turn','mode'],'additionalProperties':False}
PROXY={'type':'object','properties':{'proxy':{'type':'string','maxLength':2048}},
       'required':['proxy'],'additionalProperties':False}


def proxy_settings():
    path=codex_home()/'auto-resume/preferences.json'
    settings=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(settings,dict):raise ValueError('preferences.json 必须是 JSON 对象')
    return {'proxy':validate_proxy(settings.get('proxy','inherit')),
            'environment_override':'CODEX_RESUME_PROXY' in os.environ}


def tools():
    return [
        {'name':'open_resume_panel','title':'恢复控制台','description':'在桌面内打开额度恢复控制面板，查看任务、恢复方式和追加 prompt。',
         'inputSchema':EMPTY,'annotations':{'readOnlyHint':True},
         '_meta':{'ui':{'resourceUri':URI,'visibility':['model','app']},
                  'openai/ui':{'entrypoints':[{'type':'global'},{'type':'thread'}]},
                  'openai/outputTemplate':URI}},
        {'name':'list_resume_jobs','description':'刷新本机恢复任务状态','inputSchema':EMPTY,
         'annotations':{'readOnlyHint':True},'_meta':{'ui':{'visibility':['app']}}},
        {'name':'set_resume_control','description':'保存用户在面板中选择的单个任务的恢复设置',
         'inputSchema':CONTROL,'annotations':{'readOnlyHint':False,'destructiveHint':False},
         '_meta':{'ui':{'visibility':['app']}}},
        {'name':'set_resume_proxy','description':'保存用户在右侧面板选择的本机恢复代理配置，下次连接时生效',
         'inputSchema':PROXY,'annotations':{'readOnlyHint':False,'destructiveHint':False},
         '_meta':{'ui':{'visibility':['app']}}}]


def dispatch(method,params):
    if method=='initialize':
        return {'protocolVersion':'2025-06-18','capabilities':{'tools':{},'resources':{},
                'extensions':{'io.modelcontextprotocol/ui':{}}},'serverInfo':{'name':'codex-auto-resume','version':'0.4.0'}}
    if method=='ping':return {}
    if method=='tools/list':return {'tools':tools()}
    if method=='resources/list':return {'resources':[{'uri':URI,'name':'恢复控制台','mimeType':'text/html;profile=mcp-app'}]}
    if method=='resources/read':
        if params.get('uri')!=URI:raise ValueError('未知资源')
        return {'contents':[{'uri':URI,'mimeType':'text/html;profile=mcp-app',
                'text':Path(__file__).with_name('dashboard.html').read_text(encoding='utf-8'),
                '_meta':{'ui':{'csp':{'connectDomains':[],'resourceDomains':[]},'prefersBorder':True}}}]}
    if method=='tools/call':
        name=params.get('name');args=params.get('arguments') or {}
        try:
            if name in ('open_resume_panel','list_resume_jobs'):
                data={'jobs':jobs(),'capabilities':{'desktop_unlock':True}}
                # 配置错误不应隐藏任务列表，保留面板中的诊断及修改入口。
                try:data['network']=proxy_settings()
                except (OSError,ValueError) as error:data['network']={'error':str(error)}
            elif name=='set_resume_control':data={'control':apply_control(args)}
            elif name=='set_resume_proxy':
                if (not isinstance(args,dict) or set(args)!={'proxy'}
                        or not isinstance(args['proxy'],str) or len(args['proxy'])>2048):
                    raise ValueError('代理配置参数无效')
                value=validate_proxy(args['proxy'])
                save_preferences(codex_home(),{'proxy':value})
                data={'network':proxy_settings()}
            else:raise ValueError('未知工具')
            return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'structuredContent':data}
        except (OSError,ValueError,KeyError,TypeError) as e:
            return {'isError':True,'content':[{'type':'text','text':str(e)}]}
    raise ValueError('未知方法')


def main():
    for line in sys.stdin:
        try:
            request=json.loads(line)
            if not isinstance(request,dict):raise ValueError('请求必须是对象')
            if 'id' not in request:continue
            try:result={'result':dispatch(request.get('method'),request.get('params') or {})}
            except (ValueError,KeyError,TypeError) as e:result={'error':{'code':-32602,'message':str(e)}}
            print(json.dumps({'jsonrpc':'2.0','id':request['id'],**result},ensure_ascii=False),flush=True)
        except (ValueError,TypeError):
            print(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON request'}}),flush=True)


if __name__=='__main__':main()
