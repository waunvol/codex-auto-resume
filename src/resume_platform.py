"""Windows/Linux 文件锁、进程及用户目录适配，仅依赖标准库。"""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()


def state_root():
    return Path(os.environ.get('CODEX_RESUME_HOOK_ROOT') or codex_home() / 'auto-resume-state')


def atomic_json(path, data):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        atomic_replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def save_preferences(home, changes):
    """安装器及内嵌面板共用偏好文件，持锁合并，保留其他设置。"""
    target = home.expanduser().resolve() / 'auto-resume'
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (target / 'install.lock').open('a+b') as lock:
        lock_file(lock, blocking=True)
        path = target / 'preferences.json'
        settings = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(settings, dict):
            raise ValueError('preferences.json 必须是 JSON 对象')
        atomic_json(path, {**settings, **changes})
    return path


def validate_proxy(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('proxy 必须是 HTTP(S) 代理地址、inherit 或 direct')
    value = value.strip()
    if value in ('inherit', 'direct'):
        return value
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                 and (parsed.port is None or parsed.port > 0)
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                 and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('proxy 地址无效，请使用 http://主机:端口 或 https://主机:端口')
    return value


def codex_environment():
    """每次连接前读取代理设置，不修改宿主或其他任务的进程环境。"""
    env = dict(os.environ)
    value = env.get('CODEX_RESUME_PROXY')
    if value is None:
        path = codex_home() / 'auto-resume/preferences.json'
        settings = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(settings, dict):
            raise ValueError('preferences.json 必须是 JSON 对象')
        value = settings.get('proxy', 'inherit')
    value = validate_proxy(value)
    if value == 'inherit':
        return env
    # 同时处理大小写及 ALL_PROXY，避免旧环境盖过显式选择。
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(key, None)
    if value != 'direct':
        env.update({key: value for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')})
        bypass = env.get('NO_PROXY', env.get('no_proxy', 'localhost,127.0.0.1,::1'))
        env.update(NO_PROXY=bypass, no_proxy=bypass)
    return env


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


def desktop_candidates():
    candidates = []
    configured = os.environ.get('CODEX_CLI_PATH')
    if configured:
        candidates.append(Path(configured))
    if os.name == 'nt':
        local = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))
        for name in ('Codex', 'ChatGPT'):
            candidates += [local/'Programs'/name/'resources/codex.exe', local/name/'app/resources/codex.exe']
        store = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'WindowsApps'
        try:
            for package in sorted(store.glob('OpenAI.Codex_*'), reverse=True):
                candidates += [package/'app/resources/codex.exe', package/'resources/codex.exe']
        except PermissionError:
            pass
        if not any(p.is_file() for p in candidates):
            # Store 安装目录通常不可枚举；查询当前用户包元数据，不提权、不扫描全盘。
            try:
                result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                    "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty InstallLocation"],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10, **quiet_options())
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        location = Path(line.strip())
                        if line.strip():
                            candidates += [location/'app/resources/codex.exe', location/'resources/codex.exe']
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        candidates += [Path('/usr/lib/chatgpt/resources/codex'), Path('/opt/Codex/resources/codex'),
                       Path('/opt/ChatGPT/resources/codex'),
                       Path.home()/'.local/share/chatgpt/resources/codex']
    return candidates


def resolve_codex(executable=None, runtime='auto'):
    candidate = executable
    if not candidate and runtime != 'desktop':
        candidate = shutil.which('codex')
    if not candidate and runtime != 'cli':
        candidate = next((str(p) for p in desktop_candidates() if p.is_file()), None)
    if not candidate:
        raise ValueError('未找到 Codex 引擎。请安装桌面 App 或 CLI，或用 --codex-executable 指向 App 自带 codex/codex.exe。')
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
    if override:
        command = json.loads(override)
    elif settings.exists():
        saved = json.loads(settings.read_text(encoding='utf-8'))
        command = saved['codex_command']
        if command and (not Path(command[0]).is_file() or
                        (len(command)>1 and command[1].endswith('.js') and not Path(command[1]).is_file())):
            command = resolve_codex(runtime=saved.get('runtime','auto'))
    else:
        command = resolve_codex()
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
