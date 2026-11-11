# Codex Auto Resume

通过用户级 `UserPromptSubmit` / `Stop` hooks 自动登记每轮聊天；只有该轮明确因 Codex 订阅额度耗尽失败时，才在额度恢复后接续。正常结束自动退出，保留日志。无需 Skill、API key 或额外 Python 包。

多个聊天使用独立状态目录。同一聊天有进程锁，重复 hook 不重复派工；用户开启新轮次时旧守候退出；恢复执行器的内部请求不会递归注册。

## 安装

前提：Python 3.10+、已安装并登录的 Codex CLI。安装在运行 Codex 的同一操作系统、同一用户账户下。无需管理员或 sudo。安装器只检测依赖，不自动安装 Python、Node 或 Codex。

Clone 本仓库后，在仓库目录执行：

**Linux**

```bash
bash install.sh
```

**Windows（PowerShell 或命令提示符）**

```powershell
py -3 -X utf8 install.py
```

也提供 `./install.ps1`，适用于允许执行本地 PowerShell 脚本的环境。Python 没有 `py` 启动器时使用 `python -X utf8 install.py`。

然后打开 Codex CLI 的 `/hooks`，**信任并启用本工具的 UserPromptSubmit、Stop**，再重启 Codex App。Codex 要求新 hook 经过信任确认；安装器不伪造哈希、不绕过该确认，也不会将“安装完成”误报成“已经信任”。这是每台电脑首次安装必须完成的一步。

默认安装到 `CODEX_HOME`（未设置时为 `~/.codex`）。支持覆盖：

```bash
python3 -X utf8 install.py --codex-home /path/to/codex-home --codex-executable /path/to/codex
```

Windows 路径带空格时使用引号：

```powershell
py -3 -X utf8 install.py --codex-executable "C:\Tools\Codex\codex.exe"
```

支持原生 `codex.exe`，以及官方 npm 安装生成的 `codex.cmd`（安装器找到配套 Node + JS 入口后直接运行，避免经过 cmd 转义）。不支持任意第三方 `.bat` 包装器。自定义 `--codex-home` 必须与运行 Codex 时的 `CODEX_HOME` 一致。

## 安装器做什么

- 将运行文件复制到 `<CODEX_HOME>/auto-resume/versions/<内容哈希>/`；不依赖 clone 目录的位置，安装后可移动或删除 clone。
- 记录 Python 和 Codex 的实际路径。搬迁 Python/Codex 后重新运行安装器。
- 备份并合并 `<CODEX_HOME>/hooks.json`，保留其他 hooks 和配置；重复安装不重复增加条目。
- 内容变更时生成新运行目录，避免覆盖正在运行的旧代码；更新后可能需要重新信任新 hook 命令。
- 不复制登录凭据、个人聊天、项目文件或本机验证日志；新电脑需自行登录 Codex。

如果迁移旧版手写 hook，可以显式传 `--replace-hook-command "旧的完整 command"`。只替换完全匹配的命令，不自动猜测或删除其他插件。

## 状态与日志

默认目录：

```text
<CODEX_HOME>/auto-resume-state/<session_id>/<turn_id>/
  event.json       登记信息（不重复保存用户 prompt）
  state.json       任务状态
  runner.log      后台日志
  Stop.json       收到结束 hook 的记录（若有）
  error.txt       执行器异常（若有）
```

可通过 `CODEX_RESUME_HOOK_ROOT` 指定状态目录。Windows 的 `CODEX_HOME` 默认为 `%USERPROFILE%\.codex`，Linux 为 `$HOME/.codex`。

`Stop` 并非最终成功的事实，其他 hook 可能要求模型继续。因此后台以该轮真实的完成记录注销；不会因为 Stop 先到达就漏掉额度错误。

`watching`：本地守候，不调用模型；`waiting_quota`：等待额度；`complete`：正常退出；`cancelled`：用户已开启新轮次；`blocked`：需要人工处理。注销指退出活动执行，不删除记录。

运行目录可在 `<CODEX_HOME>/auto-resume/install.json` 的 `runtime` 中找到。使用其中的脚本查询状态：

```bash
python3 /实际运行目录/codex_auto_resume.py status --state /实际任务目录/state.json
```

`runner_alive: true` 才表示执行器持有锁；仅有状态文件不表示在运行。机器重启或进程被终止后，可对仍未完成的任务手工执行同一脚本的 `run --state ... --background`。本工具没有安装开机服务。

## 权限与边界

- 只对明确的 `usage_limit_exceeded` 事件自动恢复；人工停止、权限不足或其他错误不会盲目重试。
- 恢复使用 CLI 环境，桌面专属工具不保证可用。沿用原模型，若该模型在 CLI 不可用会报错，需要人工处理。
- 自动 hook 默认只读；仅在原轮次明确使用 workspace-write 时允许同一工作目录写入。不继承 bypass、额外写目录或网络权限；新电脑要在允许的目录中运行。
- 依赖机器开机和后台进程存活，不保证服务器重置后的零延迟。不会重置额度、购买额度或切换账户。
- 原始 rollout 不是稳定公共协议，Codex 更新后应运行验证；未知格式不会猜测并执行任务。
- 单聊天不能一边由自动执行器接管，一边手动并发派工。
- 本仓库只包含额度恢复与 hooks。DSH 是可选外部集成：使用原执行器的 `--dsh-job` 时需设置 `DSH_COLLAB_RUNNER` 指向另行安装的 DSH 执行器。该集成的 Windows 兼容性不在本仓库范围内。

## 更新和卸载

更新仓库后重新执行安装脚本即可。卸载只移除本工具拥有的 hooks，保留历史状态、备份及版本文件：

```bash
python3 -X utf8 install.py --uninstall
```

Windows 使用 `py -3 -X utf8 install.py --uninstall`。卸载不会强制终止已接管的任务，应先让活动任务结束；重启 Codex 后不再登记新任务。

## 验证

```bash
python3 -X utf8 -m unittest discover -s tests -v
```

测试不访问账户，不消耗模型额度。覆盖：跨进程锁、四聊天并发、两任务额度恢复、正常注销、人工接管、重复事件、递归抑制、安装/升级/卸载与中断恢复、中文和含空格路径。

GitHub Actions 已配置 `ubuntu-latest` / `windows-latest` 与 Python 3.10 / 3.13。Windows 额外执行真实 msvcrt 锁、后台进程和 PowerShell hook 命令测试。**开发时只有 Linux 主机，Windows 原生测试须由 Windows CI 或目标机器执行，不能把配置了 CI 当成 Windows 已通过。**跨实际五小时重置也未作为本仓库自动测试。

参考：[Codex Hooks](https://learn.chatgpt.com/docs/hooks)、[App Server](https://learn.chatgpt.com/docs/app-server)。
