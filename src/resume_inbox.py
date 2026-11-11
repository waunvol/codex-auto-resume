"""任务标题命名的可编辑 prompt 文件，仅清理本工具创建且已消费的文件。"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from resume_platform import codex_home
from resume_control import read_control, write_control


def task_title(event):
    explicit = event.get('title') or event.get('task_title')
    if explicit:
        return str(explicit)
    for database in sorted(codex_home().glob('state_*.sqlite'), reverse=True):
        try:
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=.2)) as db:
                row=db.execute('SELECT title FROM threads WHERE id=?',(event['session_id'],)).fetchone()
                if row and row[0]:return str(row[0])
        except sqlite3.Error:
            continue
    lines=str(event.get('prompt','')).strip().splitlines()
    return lines[0] if lines else 'Codex任务'


def directory():
    value=os.environ.get('CODEX_RESUME_PROMPT_DIR')
    settings=codex_home()/'auto-resume/preferences.json'
    if not value and settings.exists():
        value=json.loads(settings.read_text(encoding='utf-8')).get('prompt_dir')
    return Path(value).expanduser().resolve() if value else None


def create_prompt(job, title):
    root=directory()
    if root is None or job.state.get('inbox_file'):return
    root.mkdir(parents=True,exist_ok=True)
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',title).strip(' .')[:70] or 'Codex任务'
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        name='任务_'+name
    suffix=hashlib.sha256((job.state['thread_id']+'\n'+str(job.path)).encode()).hexdigest()[:12]
    path=root/(name+'.md')
    for candidate in (path,root/(name+'--'+suffix+'.md')):
        try:
            with candidate.open('x',encoding='utf-8'):pass
            path=candidate;break
        except FileExistsError:
            continue
    else:
        raise FileExistsError('任务 prompt 文件已存在，拒绝覆盖：'+str(candidate))
    job.save(title=title,inbox_file=str(path))
    old=read_control(job.path)
    if not old.get('prompt_file'):
        write_control(job.path,old['mode'],old['prompt'],old['approved'],str(path))


def submitted_snapshot(state,control,text):
    path=state.get('inbox_file')
    if path and control.get('prompt_file')==path:
        return {'path':path,'digest':hashlib.sha256(text.encode('utf-8')).hexdigest(),'revision':control['revision']}
    return None


def clean_prompt(job, consumed=False):
    """领取文件后比对，期间新建或修改的内容均保留。用户手工绑定文件不删除。"""
    snapshot=job.state.get('inbox_submission') if consumed else None
    path=job.state.get('inbox_file')
    if not path:return
    file=Path(path)
    expected=snapshot['digest'] if snapshot else hashlib.sha256(b'').hexdigest()
    if consumed and not snapshot:return
    claimed=file.with_name('.'+file.name+'.checking-'+uuid.uuid4().hex)
    try:
        file.rename(claimed)
    except FileNotFoundError:return
    except OSError as e:
        print('保留 prompt 文件，暂时无法清理：'+str(e),flush=True);return
    try:
        digest=hashlib.sha256(claimed.read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()
        if digest!=expected:
            # 硬链接以“不覆盖新文件”的方式归还，无法归还则保留 claimed 并记录位置。
            try:os.link(claimed,file)
            except OSError:
                print('保留未消费指令：'+str(claimed),flush=True);return
            claimed.unlink();return
        claimed.unlink()
        # 只清除此版本的自动绑定，不覆盖面板或 CLI 并发保存的新设置。
        from resume_control import clear_binding
        clear_binding(job.path,path,snapshot['revision'] if snapshot else None)
        job.save(inbox_consumed=consumed, inbox_submission=None)
    except (OSError,ValueError) as e:
        if claimed.exists():
            try:os.link(claimed,file);claimed.unlink()
            except OSError:pass
        print('未清理 prompt，内容保留：'+str(e)+' '+str(claimed),flush=True)
