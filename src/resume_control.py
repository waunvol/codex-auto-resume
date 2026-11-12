"""独立控制文件：面板写控制意图，执行器独占写任务状态。"""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import uuid
from resume_platform import atomic_replace, lock_file

DEFAULT = {'mode': 'auto', 'prompt': '', 'approved': False, 'revision': None, 'prompt_file': None}


def read_control(state_path):
    path = Path(state_path).with_name('control.json')
    return {**DEFAULT, **json.loads(path.read_text(encoding='utf-8'))} if path.exists() else dict(DEFAULT)


def write_control(state_path, mode, prompt, approve=False, prompt_file=None):
    if mode not in ('auto', 'confirm', 'off') or not isinstance(prompt, str) or len(prompt)>20000:
        raise ValueError('恢复模式无效，或 prompt 超过 20000 字符')
    if not isinstance(approve, bool):
        raise ValueError('approve 必须是布尔值')
    if prompt_file is not None and (not isinstance(prompt_file,str) or not Path(prompt_file).is_absolute()):
        raise ValueError('prompt 文件必须使用绝对路径')
    path = Path(state_path).with_name('control.json')
    with path.with_suffix('.lock').open('a+b') as lock:
        lock_file(lock, blocking=True)
        control = {'mode': mode, 'prompt': prompt, 'approved': approve, 'revision': str(uuid.uuid4()), 'prompt_file': prompt_file}
        store_control(path,control)
        return control


def store_control(path, control):
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(control,stream,ensure_ascii=False)
            stream.flush();os.fsync(stream.fileno())
        atomic_replace(temp,path)
    finally:
        Path(temp).unlink(missing_ok=True)


def clear_binding(state_path, file, revision=None):
    path=Path(state_path).with_name('control.json')
    with path.with_suffix('.lock').open('a+b') as lock:
        lock_file(lock,blocking=True)
        control=read_control(state_path)
        if control.get('prompt_file')==file and (revision is None or control['revision']==revision):
            store_control(path,{**control,'prompt_file':None})


def permitted(state_path, applied=None):
    c = read_control(state_path)
    return c['mode']=='auto' or (c['mode']=='confirm' and c['approved'] and c['revision']!=applied)


def prompt_snapshot(control):
    if control.get('prompt_file'):
        text = Path(control['prompt_file']).read_text(encoding='utf-8-sig')
        if len(text)>20000:
            raise ValueError('prompt 文件超过 20000 字符')
        return text, str(control['revision'])+':'+hashlib.sha256(text.encode('utf-8')).hexdigest()
    return control['prompt'], control['revision']


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='设置单个任务的恢复模式，绑定恢复前读取的 UTF-8 prompt 文件')
    parser.add_argument('--list',action='store_true',help='列出任务及其状态文件路径')
    parser.add_argument('--state',type=Path)
    parser.add_argument('--mode',choices=['auto','confirm','off'])
    parser.add_argument('--prompt-file',type=Path)
    parser.add_argument('--approve',action='store_true')
    args=parser.parse_args()
    if args.list:
        from resume_dashboard import jobs
        print(json.dumps({'jobs':jobs()},ensure_ascii=False,indent=2))
        parser.exit()
    if not args.state or not args.mode:
        parser.error('设置任务时需要 --state 和 --mode')
    try:
        state=json.loads(args.state.read_text(encoding='utf-8'))
        if state['status'] not in ('watching','pending','running','waiting_quota','waiting_thread','waiting_dsh','dsh_action'):
            raise ValueError('任务已结束，不能重新激活')
        file=str(args.prompt_file.expanduser().resolve(strict=True)) if args.prompt_file else None
        current=read_control(args.state)
        result=write_control(args.state,args.mode,current['prompt'],args.approve,file or current.get('prompt_file'))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError) as e:
        parser.exit(1,str(e)+'\n')
