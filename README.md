# Codex Auto Resume

Codex 因为额度用完停下来后，让它在额度恢复时接着干活。

支持 Linux 和 Windows。你可以只用桌面 App、只用 CLI，或者两者都用；不需要 Skill、API key，也不需要额外的 Python 包。**只用桌面 App 的话，不用另外安装 CLI。**

安装并启用 hooks 后，每轮任务会自动登记。正常结束就退出守候；只有明确因为额度用完而中断，才会等待恢复。多个聊天各管各的，不会把任务混在一起。

## 先装起来

需要 Python 3.10 或更新版本，以及已经登录的 Codex 桌面 App 或 CLI。安装器不会替你安装这些程序，也不需要管理员权限。

先下载仓库并进入目录：

```bash
git clone https://github.com/waunvol/codex-auto-resume.git
cd codex-auto-resume
```

### 我用桌面 App

Linux：

```bash
bash install.sh --runtime desktop --plugin
```

Windows，在 PowerShell 中执行：

```powershell
py -3 -X utf8 install.py --runtime desktop --plugin
```

然后做两件事：

1. 在 App 的 **Settings > Hooks** 中，检查并信任 `UserPromptSubmit`、`Stop` 两个 hooks。也可以从输入框附近的 **Review hooks** 进入。
2. 重启已经打开的 App，再开始任务。

插件和 hooks 都要启用：插件负责显示控制台，hooks 负责自动登记任务。安装不会补登记此前已经开始的轮次。

### 我只用 CLI

Linux：

```bash
bash install.sh --runtime cli
```

Windows：

```powershell
py -3 -X utf8 install.py --runtime cli
```

运行 `codex`，在 `/hooks` 中检查、信任并启用两个 hooks。之后照常使用就会自动登记任务。CLI 也可以完全通过文件填写恢复指令，下面有用法。

安装请使用运行 Codex 的同一个系统用户。WSL 中的 CLI 和 Windows App 应分别安装，不要共用状态目录。省略 `--runtime` 时，安装器会先找 PATH 中的 CLI，再找桌面 App 自带的引擎。

## 装好后怎么用

照常给 Codex 派任务即可，不用每次手工登记。额度用完后，脚本会按服务端给出的重置时间等待，到点再检查。

- **自动恢复**：额度可用就接着做；同一个未完成任务可以反复恢复，没有次数限制。
- **先等待我的确认**：额度恢复后也先等你，点“确认下一次恢复”才会提交。这是一次性确认。
- **停用自动恢复**：保留记录，不再提交后续恢复请求。已经开始执行的轮次，请用 Codex 的停止按钮处理。

正常回复结束、你手动停止，或者遇到权限等其他问题，都不会当作额度耗尽一直重试。“本轮已结束”只表示这轮回复结束，不代表整个项目已经做完。

打开控制台、刷新列表、保存设置和等待额度，都是本地程序在做，不调用模型。**真正恢复任务后，模型工作仍会使用你的 Codex 额度。** 通过聊天让 AI 打开控制台的那次请求，也属于普通对话，会使用额度。

恢复不是精确到毫秒：脚本到重置时间后会复查额度；需要重试时，间隔为 **30 秒**。电脑要保持唤醒，后台进程和所需代理要正常运行。

## 在桌面里打开控制台

支持静态入口的客户端，可以点击侧栏或当前聊天侧面板中的 **恢复控制台**，不用新建聊天，也不用发消息。需要时打开，用完关掉；面板关着，后台仍然会守候。

较旧客户端可能没有这些入口。可以更新客户端；也可以在原聊天中选择插件，要求“打开恢复控制台”。如果出现内嵌卡片，点标题旁的展开图标，可放到右侧。

**额度用完不会自动让一个关闭的面板弹出来。** 需要时自己点入口打开即可。已经打开的列表每 3 秒刷新，正在编辑的内容不会被刷新覆盖。

默认只显示“待恢复 / 需处理”。想看正在工作或历史轮次，切到“全部任务”。每张任务卡片的恢复方式、自定义指令和解锁授权都是独立的。

### 恢复时到底发送什么

不填自定义 prompt 时，只发送：

```text
继续任务
```

填写了自定义 prompt 时，发送：

```text
继续任务

你填写的内容
```

不会再附加长篇恢复规则或检查点摘要，原聊天上下文继续保留。同一版自定义内容只追加一次；已经追加过且你没有修改，下一次只发送“继续任务”。修改后保存，会在下次提交时使用新内容。

“保存设置与指令”只保存，不等于确认执行。选择确认模式时，还要点“确认下一次恢复”。已提交的轮次不会被后来保存的内容改写。

CLI 手工登记全新任务时，首次仍会发送 `--task` 中的任务单；之后的恢复才使用上述简短内容。

### 卡在“等待原客户端释放聊天”怎么办

桌面 App 即使已经停止回复，也可能继续持有该聊天的写锁，让后台脚本无法接手。想让脚本自动处理，可以：

1. 在该任务卡片勾选 **允许临时归档并还原原聊天，自动释放桌面写锁**。
2. 点“保存设置与指令”。如果已经在等锁且守候在线，也可以直接点 **释放聊天锁**，保存同一授权，等下一次检查处理。

工作中的任务要先切到“全部任务”才能看到。释放按钮只有在“等待聊天锁且守候在线”时才能点；其他时候变灰是正常的。确认模式下，授权解锁后仍需确认下一次恢复。

实际顺序是：**确认额度可用 → 发现写锁 → 核对原轮次已结束 → 临时归档 → 立即还原 → 原聊天继续执行。** 等待额度期间不会提前归档，也不会等任务做完才还原。聊天 ID 和历史都保留，界面上可能短暂消失一下。

授权只针对这次登记的未完成任务，后续额度窗口继续有效。你在同一聊天开始新的用户轮次后，会重新登记，需要时再授权。**交接时不要同时向这个聊天发送消息或点继续。** 正常使用其他软件不影响交接；其他聊天会共享账户额度。

这项功能需要原桌面 App 保持运行，并提供本地控制管道。聊天仍在执行、已经有新轮次、锁属于其他客户端，或有原生 Codex 子会话时，脚本会拒绝自动归档。DSH 的外部会话不属于这里的原生子会话。Windows 管道已有实现，但尚未做 Windows 桌面实机验证。

如果归档后未能确认还原，脚本会停止续跑，保留 `desktop_unlock_pending` 检查点。先检查 App 的已归档聊天和 `runner.log`；重新运行同一状态文件时，会优先尝试还原，不会直接开始新工作。

## 需要 Clash 等代理时

Codex 平时需要走代理，恢复脚本通常也需要。端口以你自己的配置为准，下面的 `7890` 只是示例。

桌面端：在恢复控制台顶部打开 **连接与代理设置**，选择“指定代理地址”，填写例如 `http://127.0.0.1:7890`，再点“保存代理设置”。不用另开网页或聊天。

CLI：在仓库目录执行：

```bash
python3 -X utf8 install.py --configure-proxy http://127.0.0.1:7890
```

Windows 把 `python3` 换成 `py -3`。这个命令只保存设置，不重装 hooks，也不启动模型。

另外两种选项：

- `inherit`：继承启动时的代理环境，默认就是这个。
- `direct`：移除 HTTP/HTTPS/ALL_PROXY 环境代理，直接连接。

设置在下次建立连接时读取，不会打断正在执行的任务。保存值适用于同一 Codex 用户下的恢复任务。如果设置了 `CODEX_RESUME_PROXY` 环境变量，它优先于保存值，面板会提醒。

也可以编辑 `<CODEX_HOME>/auto-resume/preferences.json`，只修改 `proxy`，保留已有的其他字段：

```json
{
  "proxy": "http://127.0.0.1:7890"
}
```

支持 HTTP(S) 代理地址。配置无效会显示错误，不会悄悄换成直连。只在另一个终端修改环境变量，不会改变已经启动的 App 环境。

## CLI：直接编辑任务文件

想省去找状态文件、逐个绑定的麻烦，安装时指定一个目录即可。

Linux：

```bash
bash install.sh --runtime cli --prompt-dir "$HOME/codex-resume-prompts" --no-dashboard
```

Windows：

```powershell
py -3 -X utf8 install.py --runtime cli --prompt-dir "$env:USERPROFILE\codex-resume-prompts" --no-dashboard
```

启用 hooks 后，每轮任务会在目录里生成一个 `任务标题.md`。直接编辑并保存，脚本会在恢复提交前读取。留空就用默认的“继续任务”。同名任务会加短标识，非法文件名字符会替换，不会覆盖别的任务。

这个目录也可以和桌面插件一起用：安装时同时传 `--runtime desktop --plugin --prompt-dir /你的目录`。以后升级时不再传目录，也会保留原设置；`CODEX_RESUME_PROMPT_DIR` 环境变量可以覆盖它。

文件什么时候删除：

- 恢复轮次成功结束后，删除本工具创建、已消费且没有被再次修改的文件。
- 再次额度失败或执行失败时保留；提交后又修改的内容也保留。
- 原任务正常结束时，没写过的空文件会清理，写过但未消费的内容会保留。
- 你手工绑定的自有文件不会删除。并发编辑冲突时可能留下 `.checking-` 副本，位置记在日志中。

### 已经有自己的指令文件

在仓库目录列出任务，找到对应的 `state_file`：

```bash
python3 -X utf8 src/resume_control.py --list
```

绑定一个 UTF-8 文件，先等自己确认：

```bash
python3 -X utf8 src/resume_control.py \
  --state /实际任务目录/state.json \
  --mode confirm --prompt-file /你的目录/恢复指令.md
```

准备好了再批准：

```bash
python3 -X utf8 src/resume_control.py \
  --state /实际任务目录/state.json --mode confirm --approve
```

Windows 示例：

```powershell
py -3 -X utf8 src/resume_control.py --state "C:\任务目录\state.json" --mode confirm --prompt-file "C:\指令目录\恢复指令.md" --approve
```

`--mode auto` 改回自动，`--mode off` 停用后续恢复。省略 `--prompt-file` 会保留原绑定。文件支持 UTF-8 BOM，最多 20000 字符；等待额度期间可以继续编辑。文件不存在或读取失败会报错，不会忽略。通过面板保存文字，会解除文件绑定，改用面板内容。

也可以加 `--desktop-unlock` 授权桌面解锁，用 `--no-desktop-unlock` 撤销。这仍要求执行器继承原桌面的控制管道，不能用它强行关闭其他客户端。

## 可选：浏览器控制面板

桌面插件安装方式不启动网页服务。纯 CLI 默认会启动一个只监听 `127.0.0.1` 的面板；不想要就加 `--no-dashboard`。

想打开浏览器面板，或忘了链接，在仓库目录执行：

```bash
python3 -X utf8 install.py --dashboard
```

Windows 用 `py -3 -X utf8 install.py --dashboard`。它会显示或启动面板，不重新安装 hooks。完整链接包含 `#` 后的访问凭据，只供本机使用，不要分享。

网页面板可以查看任务、选择模式和填写指令；代理设置和桌面解锁控件请用桌面内嵌面板，或上面的 CLI 命令。

## 没按预期恢复，先看这里

| 看到的情况 | 先做什么 |
| --- | --- |
| 找不到任务 | 确认 hooks 已启用，重启 App 后再开始一轮任务；默认列表不显示正在工作的任务，切到“全部任务”看看 |
| 等待额度重置 | 等到服务端给出的时间；多个额度窗口用完时，要等最后一个恢复 |
| 等待原客户端释放聊天 | 按上面的步骤授权桌面解锁；原聊天还在执行时先等它结束 |
| 无活动守候进程 | 先看状态；本轮正常结束后没有守候是正常的，等待中的任务才需要排查进程是否退出 |
| 需人工处理 / blocked | 查看卡片说明和 `runner.log`；权限、模型、工具或登录问题不会无限重试，处理后从原聊天继续 |
| 历史轮次卡住 | 默认只显示最新登记，切到“全部任务”才显示历史；不要给旧轮次重复启动恢复 |
| 控制台超时、Transport closed，或新功能显示接口未更新 | 等活动任务结束后重启客户端，加载新版插件连接；有些版本的 MCP 列表没有单独的重启按钮 |

只有明确的额度耗尽才自动恢复。你手动停止或从原聊天发了新消息，旧轮次的守候会停止；启用的 hooks 会为新用户轮次重新登记。等待额度或写锁时，临时网络错误会按 30 秒间隔重试，认证等错误仍需人工处理。

## 文件在哪，电脑重启后怎么办

`CODEX_HOME` 默认是 Linux 的 `~/.codex` 或 Windows 的 `%USERPROFILE%\.codex`。使用自定义目录时，安装加 `--codex-home "实际目录"`，并让 App 使用同一目录。

```text
<CODEX_HOME>/auto-resume/
  install.json                    实际运行目录、引擎位置
  preferences.json                代理、自动指令文件目录
  versions/<版本>/                已安装运行文件
  hooks.backup-*.json              安装前的 hooks 备份
  dashboard.json / dashboard.log  网页面板连接信息、日志
<CODEX_HOME>/auto-resume-state/<聊天ID>/<轮次ID>/
  event.json                      登记信息
  state.json                      执行器状态
  control.json                    恢复模式、自定义 prompt、解锁授权
  runner.log / error.txt           运行日志、异常信息
```

这些文件可能含任务资料和自定义指令，请按个人工作数据保管。`CODEX_RESUME_HOOK_ROOT` 可以改任务记录目录，App、脚本和面板需要使用同一设置。

本工具没有安装开机服务。电脑重启后，尚未结束的守候需要从原状态文件重新启动；`install.json` 里的 `runtime` 就是下面的“实际运行目录”：

```bash
python3 -X utf8 /实际运行目录/codex_auto_resume.py run \
  --state /实际任务目录/state.json --background
```

Windows 将 `python3` 换成 `py -3`。重复启动会被锁拦住，避免同一聊天跑两份。已经完成、撤销或 blocked 的任务不会因此盲目重新执行；有未完成的还原检查点时，会先处理还原。

## 更新和卸载

更新时，在仓库目录执行 `git pull`，再执行原来的安装命令。安装器会备份、合并 hooks，保留其他插件、代理和指令目录设置；新 hook 命令可能需要重新信任。**最省心的做法是等当前任务结束后更新，再重启客户端。**

新运行文件会放到独立版本目录。修改 clone 中的源码或重新安装，都不会让已经运行的旧守候自动加载新版。确实需要迁移未结束的登记时：先确认它没有在执行恢复轮次，停止对应的旧守候进程，再使用新版 `runtime` 和原 `state.json` 按上一节启动。不要停止正在干活的模型进程，也不要另建重复登记。

旧网页面板服务也可能仍是旧版；确认 PID 后关闭对应面板服务，再运行 `--dashboard`。桌面插件旧连接则等空闲后重启客户端。

安装后运行文件不依赖 clone 路径，可以移动或删除 clone。卸载 hooks 用：

```bash
python3 -X utf8 install.py --uninstall
```

Windows 用 `py -3 -X utf8 install.py --uninstall`。历史记录和备份会保留，活动任务不会被强制停止。内嵌插件请另外在 App 插件列表卸载 **Codex 恢复控制台**。重启 App 后不再登记新任务。

## 自动找不到 Codex 引擎

指定 App 内部的 `codex` / `codex.exe`，不要指定桌面 GUI 主程序。下面路径只是示例，请换成实际安装位置。

Linux：

```bash
bash install.sh --runtime desktop --plugin --codex-executable /usr/lib/chatgpt/resources/codex
```

Windows：

```powershell
py -3 -X utf8 install.py --runtime desktop --plugin --codex-executable "C:\实际安装目录\resources\codex.exe"
```

支持已知 Linux 桌面目录、Windows 用户安装目录及当前用户的 `OpenAI.Codex` Store 包。官方 npm `codex.cmd` 也可以使用；任意第三方 `.bat` 包装器不支持。迁移旧手写 hook 时，可用 `--replace-hook-command "旧的完整 command"`，只替换完全匹配的命令。

## 其他边界和 DSH 对接

恢复不会增加额度、购买额度或切换账户，而是沿用当前用户的本地登录。桌面专属动态工具不一定能在后台恢复进程中使用；缺少工具、模型或授权时会停下来。

自动 hooks 默认只读；原轮次明确允许 workspace-write 时，才允许在同一工作目录写入，不继承额外写目录、bypass 或网络权限。不同聊天彼此隔离，但文件修改和账户额度仍可能互相影响。

DSH 是可选的外部执行器，普通自动恢复不用配置它。已有工作包时，通过 `DSH_COLLAB_RUNNER` 指定执行器并用 `--dsh-job 工作包ID` 绑定；项目协调器用 `DSH_PROJECT_RUNNER` 和 `--dsh-project 项目ID`，两种绑定互斥。非默认记录目录用 `--dsh-root`，首次登记会核对项目目录。

工作包还在运行时，恢复器只做本地等待；结束后由 Codex 复核、返工和验收。项目必须事先登记和授权，hooks 不会自行创建项目或扩大派工范围。DSH 的 Windows 兼容性不属于本仓库范围。

## 开发验证

运行与 CI 相同的测试：

```bash
python3 -X utf8 tests/run_ci.py
```

前端桥接回归需要 Node；普通运行不需要另外安装 Node。CI 覆盖 Linux / Windows、Python 3.10 / 3.13，并提供 Node 22。模拟恢复测试不访问真实账户、不使用模型额度，也不等于实际跑过五小时额度重置。

已验证 Linux 桌面上的真实写锁释放和同聊天脚本续跑。Windows 桌面命名管道、真实五小时窗口仍需对应环境验证。工具会读取本地轮次记录和桌面控制接口；Codex 升级后格式可能变化，遇到未知格式会报错，不会猜测执行。

相关接口：[Hooks](https://learn.chatgpt.com/docs/hooks)、[App Server](https://learn.chatgpt.com/docs/app-server)。
