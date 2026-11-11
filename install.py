#!/usr/bin/env python3
"""安装/升级/卸载用户级 hooks，不修改其他 hooks 或 Codex 信任设置。"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SOURCE = Path(__file__).resolve().parent / 'src'
sys.path.insert(0, str(SOURCE))
from resume_platform import codex_home, lock_file, resolve_codex, quiet_options, atomic_replace

FILES = ('codex_auto_resume.py', 'codex_resume_hook.py', 'resume_platform.py', 'resume_control.py', 'resume_dashboard.py', 'dashboard.html', 'resume_mcp.py', 'resume_plugin.py', 'resume_inbox.py')


def atomic_json(path, data):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        atomic_replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def hook_command(python, script, windows=None):
    if windows is None:
        windows = os.name == 'nt'
    args = [str(python), '-X', 'utf8', str(script)]
    if not windows:
        return shlex.join(args)
    # 固定 ASCII 启动命令，路径经 PowerShell 单引号和 UTF-16 编码；支持空格、中文和 &。
    expression = '& ' + ' '.join("'" + x.replace("'", "''") + "'" for x in args) + '; exit $LASTEXITCODE'
    encoded = base64.b64encode(expression.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded


def load_json(path, default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def merge_hooks(config, old_command, new_command, replace_command=None):
    # 仅移除本安装拥有的 handler，不删除同组其他 handler 或其他事件。
    hooks = config.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('hooks.json 的 hooks 字段必须是对象')
    owned = set(old_command if isinstance(old_command, list) else [old_command])
    owned.update([replace_command]); owned.discard(None)
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ValueError(f'事件 {event} 的值必须是数组')
        kept = []
        for group in groups:
            handlers = group.get('hooks', [])
            retained = [h for h in handlers if h.get('command') not in owned]
            if retained or not handlers:
                kept.append({**group, 'hooks': retained})
        hooks[event] = kept
    if new_command:
        for event in ('UserPromptSubmit', 'Stop'):
            hooks.setdefault(event, []).append({'hooks': [
                {'type': 'command', 'command': new_command, 'timeout': 5}
            ]})
    return config


def install(home, executable=None, uninstall=False, replace_command=None, runtime="auto"):
    home = home.expanduser().resolve()
    target = home / 'auto-resume'
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (target / 'install.lock').open('a+b') as lock:
        lock_file(lock, blocking=True)
        manifest_path = target / 'install.json'
        previous = load_json(manifest_path, {})
        config_path = home / 'hooks.json'
        original = config_path.read_bytes() if config_path.exists() else None
        config = load_json(config_path, {})
        command = None
        manifest = {}
        if not uninstall:
            cli = resolve_codex(executable, runtime=runtime)
            check = subprocess.run([*cli, '--version'], capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=15, **quiet_options())
            if check.returncode or not check.stdout.strip().startswith('codex'):
                raise ValueError('Codex 引擎 --version 校验失败：' + (check.stderr or check.stdout).strip())
            settings = {'codex_command': cli, 'runtime': runtime}
            digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
            for name in FILES:
                digest.update((SOURCE / name).read_bytes())
            version = target / 'versions' / digest.hexdigest()[:16]
            version.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                source, destination = SOURCE / name, version / name
                if destination.exists() and destination.read_bytes() != source.read_bytes():
                    raise ValueError(f'已安装版本被修改，请先核对：{destination}')
                if not destination.exists():
                    shutil.copyfile(source, destination)
            atomic_json(version / 'runtime-settings.json', settings)
            command = hook_command(sys.executable, version / 'codex_resume_hook.py')
            manifest = {'command': command, 'runtime': str(version), 'python': sys.executable,
                        'codex_command': cli, 'codex_version': check.stdout.strip()}
        owned = list(dict.fromkeys([*previous.get('owned_commands', []),
                                    previous.get('command'), command, replace_command]))
        owned = [x for x in owned if x]
        config = merge_hooks(config, owned, command, replace_command)
        # 每次真实配置变更保留原始字节。先写恢复清单，避免中断后失去 hook 所有权。
        encoded = (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if original != encoded:
            if original is not None:
                backup = target / ('hooks.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
                backup.write_bytes(original)
            atomic_json(manifest_path, {**manifest, 'command': command or previous.get('command'),
                                        'installed': not uninstall, 'owned_commands': owned})
            atomic_json(config_path, config)
        elif manifest:
            atomic_json(manifest_path, {**manifest, 'installed': True, 'owned_commands': owned})
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path, default=codex_home())
    parser.add_argument('--codex-executable', help='codex/codex.exe 或官方 npm codex.cmd 的绝对路径')
    parser.add_argument('--runtime', choices=['auto','desktop','cli'], default='auto', help='只安装桌面端时可选 desktop，无需另外安装 CLI')
    parser.add_argument('--prompt-dir',type=Path,help='任务开始时在此目录按标题创建可编辑的恢复 prompt 文件')
    parser.add_argument('--plugin', action='store_true', help='安装桌面内嵌 MCP Apps 插件，不启动 HTTP 面板')
    parser.add_argument('--dashboard', action='store_true', help='重新启动或显示已安装的本地面板链接')
    parser.add_argument('--no-dashboard', action='store_true', help='安装时暂不启动本地状态面板')
    parser.add_argument('--uninstall', action='store_true', help='移除本工具 hooks，保留记录和已安装版本')
    parser.add_argument('--replace-hook-command', help='迁移旧安装时，只替换完全匹配的旧 hook command')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('需要 Python 3.10 或更新版本')
    if args.dashboard:
        if args.uninstall:
            parser.error('--dashboard 不能与 --uninstall 同时使用')
        manifest = load_json(args.codex_home/'auto-resume/install.json', {})
        if not manifest.get('installed'):
            raise ValueError('请先运行安装器安装 hooks')
        from resume_dashboard import ensure_dashboard
        print(ensure_dashboard(Path(manifest['runtime'])/'resume_dashboard.py', args.codex_home))
        return 0
    result = install(args.codex_home, args.codex_executable, args.uninstall, args.replace_hook_command, args.runtime)
    if args.prompt_dir and not args.uninstall:
        prompt_dir=args.prompt_dir.expanduser().resolve(); prompt_dir.mkdir(parents=True,exist_ok=True)
        atomic_json(args.codex_home.expanduser().resolve()/'auto-resume/preferences.json',{'prompt_dir':str(prompt_dir)})
        print('任务 prompt 目录：'+str(prompt_dir))
    print('已卸载 hooks；历史记录和备份保留。' if args.uninstall else
          '安装完成。桌面端请到 Settings > Hooks 信任并启用 UserPromptSubmit、Stop；CLI 使用 /hooks。重启已打开的 App。')
    if result:
        print('运行目录：' + result['runtime'])
        if args.plugin:
            from resume_plugin import install_plugin
            print('桌面插件已安装：' + install_plugin(result['runtime'], result['codex_command'], args.codex_home))
            print('重启 App 后，从恢复控制台的侧栏或对话面板入口直接打开。')
        elif not args.no_dashboard:
            from resume_dashboard import ensure_dashboard
            print('任务控制面板（可在 Codex 内置浏览器打开）：' + ensure_dashboard(
                Path(result['runtime'])/'resume_dashboard.py', args.codex_home))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print('安装失败：' + str(error), file=sys.stderr)
        sys.exit(1)
