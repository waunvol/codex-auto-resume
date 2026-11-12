#!/usr/bin/env python3
"""本机任务控制面板，仅监听 127.0.0.1；无需模型或外部前端依赖。"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from resume_platform import codex_home, state_root, lock_file, background_options
from resume_control import read_control, write_control


def jobs():
    from codex_auto_resume import runner_alive
    result=[]
    for p in state_root().glob('*/*/state.json'):
        try:
            state=json.loads(p.read_text(encoding='utf-8'))
            result.append({'session':p.parent.parent.name,'turn':p.parent.name,
                           'state_file':str(p.resolve()), 'title':state.get('title'), 'inbox_file':state.get('inbox_file'), 'status':state['status'],'cwd':state.get('cwd'),'summary':state.get('summary'),
                           'retry_at':state.get('retry_at'),'runner_alive':runner_alive(p),
                           'applied_control_id':state.get('applied_control_id'), 'control':read_control(p)})
        except (OSError,ValueError,KeyError) as e:
            result.append({'session':p.parent.parent.name,'turn':p.parent.name,'status':'error','summary':str(e)})
    return result


def apply_control(data):
    import re
    if any(not isinstance(data.get(k),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',data[k]) for k in ('session','turn')):
        raise ValueError('任务 ID 无效')
    path=state_root()/data['session']/data['turn']/'state.json'
    # 路径始终限制在当前用户任务目录内，不能将 ID 作为任意文件路径。
    if not path.is_file():raise ValueError('任务不存在')
    if json.loads(path.read_text(encoding='utf-8'))['status'] not in ('watching','pending','running','waiting_quota','waiting_thread','waiting_dsh','dsh_action'):
        raise ValueError('任务已结束，不能重新激活历史任务')
    control=write_control(path,data.get('mode'),data.get('prompt',''),data.get('approve',False))
    return control


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def respond(self,status,data,mime='application/json; charset=utf-8'):
        body=data.encode('utf-8') if isinstance(data,str) else json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'")
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

    def authorized(self):
        origin='http://127.0.0.1:'+str(self.server.server_port)
        if self.headers.get('Host') != '127.0.0.1:'+str(self.server.server_port):return False
        if self.headers.get('Origin') not in (None,origin):return False
        return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.token)

    def do_GET(self):
        if self.path=='/':
            return self.respond(200,Path(__file__).with_name('dashboard.html').read_text(encoding='utf-8'),'text/html; charset=utf-8')
        if not self.authorized():return self.respond(403,{'error':'无效访问凭据'})
        if self.path=='/api/jobs':return self.respond(200,{'jobs':jobs()})
        if self.path=='/api/health':return self.respond(200,{'ok':True})
        self.respond(404,{'error':'不存在'})

    def do_POST(self):
        if not self.authorized():return self.respond(403,{'error':'无效访问凭据'})
        if self.path!='/api/control':return self.respond(404,{'error':'不存在'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>100000:raise ValueError('请求大小无效')
            data=json.loads(self.rfile.read(length))
            control=apply_control(data)
            self.respond(200,{'control':control})
        except (ValueError,KeyError,TypeError) as e:
            self.respond(400,{'error':str(e)})
        except OSError as e:
            self.respond(500,{'error':str(e)})


def serve():
    root=codex_home()/'auto-resume';root.mkdir(parents=True,exist_ok=True)
    with (root/'dashboard.lock').open('a+b') as lock:
        try:lock_file(lock)
        except BlockingIOError:return
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.token=secrets.token_urlsafe(32)
        info={'port':server.server_port,'token':server.token}
        path=root/'dashboard.json'
        from resume_control import atomic_replace
        temp=root/'dashboard.pending';temp.write_text(json.dumps(info),encoding='utf-8');temp.chmod(0o600)
        atomic_replace(temp,path)
        try:server.serve_forever()
        finally:server.server_close()


def ensure_dashboard(script=None, home=None):
    home=Path(home or codex_home());root=home/'auto-resume';root.mkdir(parents=True,exist_ok=True)
    def existing():
        try:
            info=json.loads((root/'dashboard.json').read_text(encoding='utf-8'))
            port=int(info['port']);token=info['token'];base=f'http://127.0.0.1:{port}'
            with urlopen(Request(base+'/api/health',headers={'Authorization':'Bearer '+token}),timeout=1) as r:
                if json.load(r).get('ok'):return base+'/#'+token
        except (OSError,ValueError,KeyError):pass
    url=existing()
    if url:return url
    with (root/'dashboard.log').open('ab') as log:
        child=subprocess.Popen([sys.executable,'-X','utf8',str(script or Path(__file__).resolve()),'--serve'],
                               env={**os.environ,'CODEX_HOME':str(home)},stdin=subprocess.DEVNULL,
                               stdout=log,stderr=log,**background_options())
    for _ in range(50):
        url=existing()
        if url:return url
        if child.poll() is not None and child.returncode:break
        time.sleep(.1)
    raise RuntimeError('面板未启动，请检查 '+str(root/'dashboard.log'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    if args.serve:serve()
    else:print(ensure_dashboard())
