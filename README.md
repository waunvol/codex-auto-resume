# Codex Auto Resume

额度用尽后守候任务，在额度恢复时按你的设置继续。支持 **只用桌面 App**、**只用 CLI** 和二者混用，无需 Skill、API key 或额外 Python 包。

每轮聊天自动登记，正常结束自动退出。多个任务独立运行。你可以在本地控制面板查看状态，选择自动恢复、等待确认或停用，填写下一次恢复时追加的 prompt。

## 先了解两个组件

- **Hooks**：由桌面端或 CLI 在任务开始、结束时触发，登记本轮任务，不消耗模型额度。
- **恢复执行器**：通过本机 Codex 引擎的 App Server 接续原会话。引擎可以来自桌面 App 自带程序，也可以来自单独安装的 CLI。

**桌面用户不需要额外安装 CLI。** 桌面端接续仍使用本机 App Server，桌面专属的动态工具不保证在恢复进程中可用；缺少工具或授权会停止并标记需要人工处理。

## 安装前准备

1. Python 3.10 或更新版本。
2. 已安装并登录桌面 App 或 CLI，使用支持的本地 hooks。
3. 在运行 Codex 的同一台电脑、同一操作系统用户下安装。WSL 内的 CLI 和 Windows App 应分别安装，不要共用状态目录。

Clone 本仓库并进入目录。安装器检测依赖，不安装 Python、Node 或 Codex，不需要管理员权限。

### 只用桌面 App

Linux：

```bash
bash install.sh --runtime desktop --plugin
```

Windows（PowerShell 或命令提示符）：

```powershell
py -3 -X utf8 install.py --runtime desktop --plugin
```

然后在桌面 App 的 **Settings > Hooks** 中审查、信任并启用 `UserPromptSubmit`、`Stop`，重启已打开的 App。也可以使用编辑框中的 **Review hooks** 入口。无需打开终端 CLI 完成信任。

使用 `--plugin` 安装后，优先从客户端的“恢复控制台”静态侧栏或对话面板入口打开，无需发送消息；具体见下方“桌面插件内嵌面板”。网页控制台是可选入口，可通过 `install.py --dashboard` 获取链接。

### 只用 CLI

Linux：

```bash
bash install.sh --runtime cli
```

Windows：

```powershell
py -3 -X utf8 install.py --runtime cli
```

运行 `codex`，在 `/hooks` 中审查、信任并启用两个 hooks。正常使用 CLI 即会自动登记；控制面板可在普通浏览器打开，不要求安装桌面 App。

省略 `--runtime` 时使用自动模式：优先 PATH 中的 CLI，再查找桌面引擎。安装器只在当前用户目录中写配置。

### 自动查找失败怎么办

Linux 支持已知桌面安装目录；Windows 支持用户安装目录和当前用户的 `OpenAI.Codex` Store 包。不同发行版或自定义安装位置可显式指定 **App 内部的 Codex 引擎**，不要指定 GUI 主程序 `ChatGPT.exe`：

```powershell
py -3 -X utf8 install.py --runtime desktop --codex-executable "C:\实际安装目录\resources\codex.exe"
```

Linux 示例：

```bash
bash install.sh --runtime desktop --codex-executable /usr/lib/chatgpt/resources/codex
```

原生 `codex.exe` 和官方 npm `codex.cmd` 均支持；npm 安装会使用配套 Node + JS 入口，不经过 cmd.exe 拼接。任意第三方 `.bat` 包装器不支持。

`CODEX_HOME` 默认是 Linux `$HOME/.codex`、Windows `%USERPROFILE%\.codex`。若 App 使用自定义目录，安装时传 `--codex-home "实际目录"`，并确保与运行 App 的环境一致。

## 桌面插件内嵌面板（推荐）

Linux：

```bash
bash install.sh --runtime desktop --plugin
```

Windows：

```powershell
py -3 -X utf8 install.py --runtime desktop --plugin
```

安装器通过 App 自带引擎执行官方本地 marketplace 安装，将 **Codex 恢复控制台** 插件加入当前用户的 Codex。插件模式不启动 HTTP 服务，也不需要 localhost 链接。CLI 引擎同样可用 `--runtime cli --plugin` 安装插件；终端客户端会得到文本工具结果。

安装后重启桌面 App：

- **全局侧栏入口“恢复控制台”**：点击即可打开待恢复任务总览，不需要新建对话或发送消息。
- **当前对话的侧面板入口“恢复控制台”**：需要时点击打开，用完关闭；不要求一直占用右侧。

这两个入口通过 OpenAI MCP Extensions 的 `global` / `thread` entrypoints 注册。点击由客户端直接调用本地工具并展示 UI，不经过模型生成回复；打开、刷新和保存设置本身不调用模型。后台额度守候在面板关闭时照常工作。

较旧客户端可能只支持普通 MCP Apps 内嵌卡片、不显示静态入口。此时应更新客户端；也可在原任务对话中选择插件并要求“打开恢复控制面板”，或使用下文的网页入口。这次普通聊天请求仍会使用正常对话额度。

需要允许插件的本地工具；自动登记仍需在 Settings > Hooks 中信任两个 hooks。插件启用与 hook 信任是两个步骤。旧客户端若不支持 MCP Apps，只能显示文本结果，应升级客户端或继续使用下文的网页面板。

插件源在 `<CODEX_HOME>/auto-resume/plugin-marketplace/`，运行文件在独立的版本目录；插件内的 MCP 配置指向当前安装版本，不依赖 clone 路径。

## CLI：按任务标题自动创建文件（推荐）

指定一次目录即可，不必再查任务状态路径或逐个绑定文件：

Linux：

```bash
bash install.sh --runtime cli --prompt-dir "$HOME/codex-resume-prompts" --no-dashboard
```

Windows：

```powershell
py -3 -X utf8 install.py --runtime cli --prompt-dir "$env:USERPROFILE\codex-resume-prompts" --no-dashboard
```

也可以与桌面插件同时安装：`--runtime desktop --plugin --prompt-dir /你的目录`。设置保存在当前 `CODEX_HOME` 中，后续升级不传目录参数也会保留。运行环境变量 `CODEX_RESUME_PROMPT_DIR` 可覆盖安装设置。

启用 hooks 后，每轮任务登记会在目录中创建 `任务标题.md`。优先使用当前 Codex 本地任务标题；标题尚未生成时使用本轮输入的第一行。标题中的路径分隔符等非法字符会替换；同名任务添加短标识，避免多任务互相覆盖。

直接编辑这个 UTF-8 文件即可，保存后在下一次恢复提交前读取。默认是自动恢复，不需要额外执行绑定命令；需要先确认时，仍可用下方的 CLI 命令或插件面板改成确认模式。

文件清理规则：

- 恢复轮次确认完成后，自动删除本工具创建且内容未再变化的已消费文件，并解除其绑定。
- 服务器再次返回额度失败、普通执行失败时保留文件，避免指令丢失。
- 你在提交后又修改了文件，新内容会保留。清理时有并发写入冲突，可能保留一个带 `.checking-` 的副本，具体位置见 `runner.log`。
- 原任务正常结束且没有用到追加指令时，空文件会清理；写过但未消费的内容会保留。
- 通过 `--prompt-file` 手工绑定的自有文件不会自动删除。

每一轮新任务生成新文件；历史保留文件不会被新任务覆盖。标题和多任务关联信息也可在插件面板中查看。

## CLI：通过文件填写恢复指令

纯终端用户无需打开任何面板。以下命令在 clone 仓库目录执行；安装后也可将 `src/` 换成 `install.json` 中的实际 `runtime` 目录。

先列出任务，找到需要处理的 `state_file`：

```bash
python3 -X utf8 src/resume_control.py --list
```

把追加指令写入自己的 UTF-8 文件，例如 `/home/me/resume-prompt.md`，然后绑定该文件并等待确认：

```bash
python3 -X utf8 src/resume_control.py \
  --state /实际任务目录/state.json \
  --mode confirm \
  --prompt-file /home/me/resume-prompt.md
```

准备好后确认下一次恢复：

```bash
python3 -X utf8 src/resume_control.py \
  --state /实际任务目录/state.json --mode confirm --approve
```

Windows 示例：

```powershell
py -3 -X utf8 src/resume_control.py --state "C:\实际任务目录\state.json" --mode confirm --prompt-file "C:\我的文件\恢复指令.md" --approve
```

文件在**真正准备提交恢复请求时**读取，因此等待额度期间可以继续编辑；修改后无需重复登记。UTF-8 BOM 也支持，最多 20000 字符。文件不存在、无法读取或编码错误时任务会 blocked，不会悄悄忽略。相同内容不会重复注入；自动模式下文件内容变化后，下次提交会使用新内容。面板内直接保存文字会切换回内嵌文字输入，解除文件绑定。

`--mode off` 停用恢复；`--mode auto` 恢复为自动执行。省略 `--prompt-file` 会保留已有绑定。`--approve` 是一次性确认，不会重置额度、重启已完成任务或中断已提交的轮次。

## 在桌面右侧常开控制台

无需为恢复功能新建对话。内嵌控制台卡片标题旁的展开按钮可将它放入侧边面板，或将本地控制台网页放入 Codex 右侧浏览器面板后保留该标签。额度耗尽后，已经打开的列表会自动刷新，不需要让模型再次打开。

列表默认显示“待恢复 / 需处理”，包含等待额度、准备恢复、blocked 和异常任务；“全部任务”可查看正常守候、执行中及历史任务。新任务登记、刷新、保存设置和确认恢复均由本地程序执行，不调用模型。通过聊天要求打开面板的那一次正常对话仍使用 Codex 额度。

恢复 prompt 为可选输入：**留空表示按原会话继续原任务**，填写则在恢复提交时附加你的指令。使用确认模式时还需点“确认下一次恢复”；保持自动模式则不需要逐次确认。

优先使用上文的静态侧栏或对话面板入口。这里描述的是可选的右侧浏览器标签入口。跨任务切换或 App 重启后的标签保留由客户端管理；窗口关闭后可用 `--dashboard` 找回本地链接。面板本身不新增 Codex 对话、用户消息或任务。

## 使用控制面板

安装结束会自动启动只监听 `127.0.0.1` 的面板，打印带 `#访问凭据` 的链接。完整链接只供本机本人使用，不要分享。面板不查询模型额度、不访问外部网页。

忘记链接或电脑重启后，在仓库目录执行：

```bash
python3 -X utf8 install.py --dashboard
```

Windows 使用 `py -3 -X utf8 install.py --dashboard`。此命令不重新安装或改变 hook 信任，只显示或重启面板。安装时不想启动网页服务可加 `--no-dashboard`。

面板每三秒刷新任务状态，编辑中的输入不会被刷新覆盖。每个任务有独立设置：

| 操作 | 效果 |
| --- | --- |
| 额度恢复后自动继续 | 默认设置，额度可用后自动提交剩余工作 |
| 先等待我的确认 | 即使额度恢复也不提交，等待你确认下一次恢复 |
| 停用自动恢复 | 保留记录及守候进程，不提交恢复请求 |
| 保存设置与指令 | 保存当前模式和追加 prompt，不隐含确认 |
| 确认下一次恢复 | 切换为确认模式，批准下一次提交；额度不足仍会等待 |

**追加 prompt 的生效时机**：用户保存的指令会在下一次恢复提交时附加到原任务，原会话上下文继续保留。同一版指令只注入一次；修改并保存会生成新版本。例如：“先检查刚才已经生成的文件，不要重复构建，完成剩余测试即可。”

“确认下一次恢复”是一次性批准。该轮结束后若仍有后续工作，继续等待你确认；选“自动继续”则允许持续推进直到完成。已发出的请求不能通过面板撤回；执行过程中保存的设置作用于下一次提交。停止正在执行的工作请使用 Codex 的停止操作。

正常结束、人工取消和 blocked 的历史任务不能在面板中重新激活。关闭页面不影响后台守候；关闭 App 也不等于停止已经独立运行的后台任务。

## 状态说明

| 状态 | 含义 |
| --- | --- |
| `watching` | 原轮次工作中，本地守候，尚未调用恢复模型 |
| `pending` | 原轮次因额度失败，准备恢复；可能被用户设置暂停 |
| `waiting_quota` | 等待额度重置，显示下次复查时间 |
| `running` | 恢复轮次已经提交执行 |
| `complete` | 任务完成，或原聊天正常结束无需恢复 |
| `cancelled` | 你已开启新轮次，旧守候撤销 |
| `blocked` | 缺少权限、工具、可用模型或需要人工决定 |

面板同时显示守候进程是否在线，以及是否停用或等待确认。仅有状态文件不代表进程仍在工作。新任务要在 hook 已启用并重启 App 后开始，安装不会补登记早先已经开始的聊天。

## 文件位置与排查

```text
<CODEX_HOME>/auto-resume/
  install.json                 运行目录和引擎位置
  versions/<内容哈希>/          已安装运行文件
  hooks.backup-*.json          安装前配置备份
  dashboard.json              本地面板端口和访问凭据
  dashboard.log               面板异常日志
<CODEX_HOME>/auto-resume-state/<session_id>/<turn_id>/
  event.json                  hook 登记信息
  state.json                  执行器状态
  control.json                用户恢复模式、追加 prompt、一次性确认
  runner.log                  后台执行日志
  Stop.json                   结束 hook 记录（若有）
  error.txt                   异常记录（若有）
```

`CODEX_RESUME_HOOK_ROOT` 可覆盖任务状态目录，设置后要让 App 和面板继承同一环境。任务输入、摘要和追加 prompt 可能含项目资料，保存在本地记录中并在恢复时发送给原模型。

原始 `Stop` 事件不代表任务最终成功，其他 Stop hook 可能要求继续。本工具会核对落盘结局再退出，保留日志，不删除历史。

机器重启后面板可重新启动；尚未结束的守候任务需从原状态文件恢复。`install.json` 中的 `runtime` 是实际安装目录：

```bash
python3 /实际运行目录/codex_auto_resume.py run --state /实际任务目录/state.json --background
```

Windows 将命令前缀换成 `py -3 -X utf8`。若已有执行器持锁，不会重复启动；`complete` / `cancelled` / `blocked` 不会盲目重试。

## 更新与卸载

拉取新版后重新执行安装命令。安装器备份并合并 hooks，保留其他插件的配置；重复安装不重复登记。运行文件会复制到独立版本目录，安装后可删除 clone；App 更新导致引擎原路径消失时，会按安装时的运行模式重新查找。

更新后新 hook 命令可能要求再次信任。旧面板若已在运行，仍使用其启动时的版本；更新面板代码后需关闭旧面板服务进程，再运行 `--dashboard`。查看 `dashboard.log` 排查启动问题。

卸载 hooks：

```bash
python3 -X utf8 install.py --uninstall
```

Windows 使用 `py -3 -X utf8 install.py --uninstall`。卸载保留记录、已安装版本和备份，不强行中断活动任务或面板服务。若安装了内嵌插件，还需在桌面插件列表卸载 **Codex 恢复控制台**；这不会影响其他插件。重启 App 后不再登记新任务。

迁移旧版手写 hook 可显式传 `--replace-hook-command "旧的完整 command"`，只替换完全匹配的命令。

## 运行边界

- 额度恢复不代表原聊天已经释放：如果桌面客户端仍占用聊天写锁，控制台会显示“等待原客户端释放聊天”，每 30 秒重试，不调用模型。关闭该聊天使其卸载或退出原客户端后，脚本才能通过独立 App Server 接手续跑；当前版本不能强行接管仍由桌面持有的聊天，也不会自动关闭客户端。其他恢复错误会明确显示为“需人工处理”。
- 只对明确的 `usage_limit_exceeded` 自动恢复；人工停止、权限不足或其他错误不自动重试。
- 需要电脑开机和用户进程存活，没有安装开机服务，不保证重置后零延迟。
- 不重置或购买额度，不切换账户；复用当前用户的本地登录信息。
- 自动 hook 默认只读；只在原轮次明确使用 workspace-write 时允许同一工作目录写入，不继承 bypass、额外写目录或网络权限。
- 原模型在当前引擎不可用、桌面动态工具缺失时会 blocked。桌面 App 与 CLI 并非所有工具都完全等价。
- 不要在自动执行器接管同一聊天时再手动并行派工。不同聊天各自隔离。
- rollout 不是稳定公共接口，Codex 更新后应运行测试；未知格式不会猜测执行。
- DSH 是可选外部集成，需通过 `DSH_COLLAB_RUNNER` 提供独立执行器；其 Windows 兼容性不属于本仓库范围。

已有多工作包协调器时，可用 `--dsh-project 项目ID` 代替 `--dsh-job 工作包ID`，两者互斥；首次登记会核对项目目录与 `--cwd` 一致。通过 `DSH_PROJECT_RUNNER` 指定 `dsh_project.py`，未指定时尝试使用 `DSH_COLLAB_RUNNER` 同目录下的文件。工作包仍在执行时只做本地等待，结束后由 Codex 复核、返工和验收整个项目；不会因单个工作包完成就把项目标记完成。项目须事先在外部协调器中登记和授权，自动 hook 不会自行创建项目或扩展派工范围。升级源码后须重新安装，旧版已安装 runtime 不会自动获得新参数。

## 开发验证

```bash
python3 -X utf8 -m unittest discover -s tests -v
```

测试使用模拟 App Server，不消耗模型额度。覆盖多聊天并发、额度恢复、正常注销、人工接管、递归抑制、文件锁、中文路径、安装/升级/卸载、桌面引擎发现、暂停确认、追加 prompt、面板认证与任务隔离。

CI 配置 Linux / Windows 和 Python 3.10 / 3.13。Windows 原生分支须由 Windows 主机或 CI 验证；模拟恢复不等于实际五小时重置已验证。

依据：[Codex Hooks 与桌面信任入口](https://learn.chatgpt.com/docs/hooks)、[App Server](https://learn.chatgpt.com/docs/app-server)。
