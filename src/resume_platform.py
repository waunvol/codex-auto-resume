"""Windows/Linux 文件锁、进程及用户目录适配，仅依赖标准库。"""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()


def state_root():
    return Path(os.environ.get('CODEX_RESUME_HOOK_ROOT') or codex_home() / 'auto-resume-state')


def lock_file(stream, blocking=False):
    fd = stream if isinstance(stream, int) else stream.fileno()
    if os.name != 'nt':
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import msvcrt
    while True:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if not blocking:
                raise BlockingIOError(str(error)) from error
            time.sleep(.05)


def unlock_file(stream):
    fd = stream if isinstance(stream, int) else stream.fileno()
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def background_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                'close_fds': True}
    return {'start_new_session': True, 'close_fds': True}


def quiet_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def resolve_codex(executable=None):
    candidate = executable or shutil.which('codex')
    if not candidate:
        raise ValueError('未找到 Codex CLI，请先安装并登录，或用 --codex-executable 指定路径。')
    path = Path(candidate).expanduser().absolute()
    if not path.is_file():
        raise ValueError(f'Codex 可执行文件不存在：{path}')
    if path.suffix.lower() in ('.cmd', '.bat'):
        # npm 官方 shim 对应的 JS 入口：不经过 cmd.exe，避免路径转义及 shell 注入。
        entry = path.parent / 'node_modules/@openai/codex/bin/codex.js'
        node = shutil.which('node')
        if entry.is_file() and node:
            return [node, str(entry)]
        raise ValueError('无法识别此 .cmd/.bat，请指定原生 codex.exe，或安装官方 npm Codex 与 Node。')
    return [str(path)]


def codex_command():
    override = os.environ.get('CODEX_RESUME_CODEX_COMMAND')
    settings = Path(__file__).with_name('runtime-settings.json')
    command = (json.loads(override) if override else
               json.loads(settings.read_text(encoding='utf-8'))['codex_command'] if settings.exists()
               else resolve_codex())
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError('codex_command 必须是非空字符串参数列表。')
    return command


def atomic_replace(source, destination):
    # Windows 短暂读句柄可能阻止替换；只对权限冲突做有界重试，不隐藏持久错误。
    deadline = time.monotonic() + 1
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if os.name != 'nt' or time.monotonic() >= deadline:
                raise
            time.sleep(.02)
