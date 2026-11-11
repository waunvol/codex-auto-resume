#!/usr/bin/env python3
"""用户级生命周期 hook：每轮独立登记，由已有执行器判断额度失败和注销。"""
import asyncio
from resume_platform import lock_file, background_options, state_root
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import codex_auto_resume as runner

ROOT = state_root()


def job_folder(event):
    identifiers = [event['session_id'], event['turn_id']]
    if any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', item) for item in identifiers):
        raise ValueError('Invalid session or turn identifier')
    return ROOT.joinpath(*identifiers)


def worker(folder):
    event = json.loads((folder / 'event.json').read_text(encoding='utf-8'))
    state_path = folder / 'state.json'
    with (folder / 'state.json.lock').open('a+b') as lock:
        lock_file(lock)
        job = runner.Job(state_path, {})
        # hook 可能早于 task_started 落盘，绑定输入中的确切轮次，不能猜上一轮。
        if state_path.exists():
            job.state = json.loads(state_path.read_text(encoding='utf-8'))
        else:
            job.save(status='watching', cwd=str(Path(event['cwd']).resolve()),
                     thread_id=event['session_id'], watch_turn_id=event['turn_id'],
                     watch_rollout=event['transcript_path'], retry_at=None, turn_id=None,
                     model=event.get('model'), workspace_write=False, allow_network=False,
                     prompt='继续本会话中因额度中断的用户任务。先核对已有成果，只完成剩余工作；'
                            '遵守原任务范围和授权，不重复已完成操作。遇到缺失权限或人工决策返回 blocked。')
            deadline = time.monotonic() + 30
            while True:
                try:
                    turn = runner.latest_watched_turn(job)
                    if turn['turn_id'] == event['turn_id']:
                        break
                except runner.Blocked:
                    pass
                if time.monotonic() >= deadline:
                    raise runner.Blocked('hook 对应轮次未落盘，未启动模型。')
                time.sleep(.2)
            # 只在原轮次明确允许项目写入时启用同目录写入，不继承 bypass 权限。
            context = None
            with Path(event['transcript_path']).open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get('type') == 'turn_context' and entry['payload'].get('turn_id') == event['turn_id']:
                        context = entry['payload']
            if context:
                policy = context.get('sandbox_policy') or {}
                job.save(workspace_write=policy.get('type') == 'workspace-write')
        if job.state['status'] not in runner.ACTIVE_STATES:
            return
        # 同一聊天的新轮次等待旧守候观察到新事件并释放锁；不同聊天互不等待。
        deadline = time.monotonic() + 10
        while True:
            try:
                job.claim_thread(event['session_id'])
                break
            except runner.RunnerError:
                if time.monotonic() >= deadline:
                    raise runner.Blocked('原会话已有执行器，未重复接管。')
                time.sleep(.2)
        try:
            asyncio.run(runner.run(job))
        finally:
            job.close()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'worker':
        folder = Path(sys.argv[2])
        try:
            worker(folder)
        except Exception as error:
            # 不掩盖失败；保留独立故障记录，不覆盖可能由其他进程持有的状态。
            (folder / 'error.txt').write_text(str(error) + '\n', encoding='utf-8')
            raise
        return
    if os.environ.get('CODEX_AUTO_RESUME_CHILD') == '1':
        print('{}')
        return
    event = json.load(sys.stdin)
    folder = job_folder(event)
    if event['hook_event_name'] == 'UserPromptSubmit':
        if not event.get('transcript_path'):
            raise ValueError('Missing transcript_path; cannot register resume')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (folder / 'register.lock').open('a+b') as lock:
            lock_file(lock, blocking=True)
            if not (folder / 'event.json').exists():
                # 不重复保存 prompt，恢复依据原会话历史。
                minimal = {k:event[k] for k in ('session_id','turn_id','cwd','transcript_path','model') if k in event}
                (folder / 'event.json').write_text(json.dumps(minimal), encoding='utf-8')
                with (folder / 'runner.log').open('ab') as log:
                    subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), 'worker', str(folder)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     **background_options())
    elif event['hook_event_name'] in ('Stop', 'Interrupt') and folder.exists():
        # Stop 不是成功的最终事实：其他 Stop hook 可能要求继续。让 worker 核对落盘结局后注销。
        (folder / (event['hook_event_name'] + '.json')).write_text(json.dumps({'turn_id':event['turn_id']}), encoding='utf-8')
    print('{}')


if __name__ == '__main__':
    main()
