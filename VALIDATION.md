# 发布前自检（2026-11-11）

- Linux 完整回归：16 项，15 项通过，1 项 Windows 原生测试按平台跳过。
- 自检修复：暂停/确认模式不阻止核对已提交轮次；SQLite 标题读取及测试显式关闭连接，避免 Windows 文件占用。
- CI 显式安装 Node 22，确保前端握手回归实际执行；运行时仍仅依赖 Python 标准库。
- 安装、升级、卸载、桌面引擎发现、MCP 协议与静态入口元数据均通过自动检查。
- `git diff --check`、Linux 安装脚本语法检查通过。没有包含本机登录凭据或任务运行数据。
- Windows 新版结果以推送后的 GitHub Actions 为准；真实五小时重置、重启后的入口按钮位置没有在本次自检中验证。

以下保留开发期间的阶段性验证记录；各阶段测试数量和安装状态描述以当时情况为准。

# 验证记录

日期：2026-11-11。

本机 Linux / Python 3.10：

- `python3 -X utf8 -m unittest discover -s tests -v`：6 项，5 项通过，1 项 Windows 原生 shell 测试按平台跳过。
- 四个并行聊天：两个额度中断各恢复一次；正常结束自动注销；人工新轮次取消旧守候。
- 重复 hook 不重复派工；自动续跑请求不递归登记。
- 跨进程文件锁互斥与释放通过。
- 安装、重复安装、配置发布中断后的重试、卸载保留外部 hooks 通过。
- 中文、空格路径通过；安装后删除源码副本，已安装运行文件仍能正常登记、注销。
- Linux `bash install.sh` 入口以真实本机 Codex `--version` 检查依赖，隔离安装、重复安装、卸载通过。
- `sh -n install.sh` 通过。

Windows：提供 msvcrt 锁、脱离控制台后台进程、UTF-8、PowerShell 编码命令、npm codex.cmd 解析实现，并提供原生测试。未在本机执行 Windows 测试。GitHub Actions 的 Windows/Linux 矩阵尚未在远程运行，上传后应确认全部通过再视为双平台实测通过。

本轮测试不使用真实模型请求，不消耗模型额度。额度恢复使用模拟 App Server，不等于真实五小时重置验证。

未发布远程仓库；没有创建提交。未改变此前电脑上的现有 hooks 安装，独立仓库通过隔离目录验证。


## 桌面独立运行与恢复控制面板（2026-11-11）

本轮基于用户已修复 Windows CI 的提交 `6ac1145` 开发，保留原 Windows 短路径与临时文件清理修复。

- Linux 宿主完整回归：9 项，8 项通过，1 项 Windows 原生 shell 测试按平台跳过。
- 新增桌面引擎发现测试：PATH 没有 CLI 时可发现桌面程序；`--runtime cli` 不隐含依赖桌面。
- 真实 Linux 桌面 App 自带引擎 `--version` 探测与隔离安装通过，无需通过 PATH 中的 CLI。
- 恢复控制测试：确认前没有账户/模型请求；确认后追加 prompt 随原任务提交，只执行一次；批准版本被记录。
- 面板 HTTP 测试：无凭据、跨来源请求拒绝；任务列表、独立控制保存、错误 ID、历史任务不得重激活通过。
- 前端 JavaScript 的 `node --check` 语法检查通过。
- Codex 内置浏览器连接两次超时，未完成视觉或桌面内点击验证；本地 HTTP 接口测试不能替代该项。
- 本轮新增 Windows 桌面安装发现与面板功能尚需推送后由 Windows CI 验证；本机没有 Windows。

未提交、推送代码，没有更新当前用户已有 hooks 安装。面板演示使用隔离目录，不调用真实模型。实际五小时重置未测试。


## 内嵌插件和 CLI 文件入口（2026-11-11）

- 完整 Linux 宿主回归：12 项，11 项通过，1 项 Windows 原生 shell 测试跳过。
- MCP stdio 初始化、工具发现、HTML UI 资源读取、任务列表、设置确认模式与 prompt、错误 ID 拒绝均通过。
- CLI prompt 文件测试覆盖 UTF-8 BOM、等待期间修改后读取新内容、内容版本变化、文件丢失报错。
- 官方桌面自带引擎在隔离 CODEX_HOME 中完成 marketplace 注册、插件安装和重复安装；源路径升级后，已安装缓存的 .mcp.json 指向新版本运行程序，验证通过。
- 内嵌页面使用 MCP Apps ui/initialize + tools/call bridge，并提供 window.openai.callTool 兼容入口；前端 JavaScript 语法检查通过。
- 插件模式无需 HTTP 监听或 localhost 链接，不依赖 Skill。CLI 文件入口无需浏览器。
- 桌面实际 iframe 渲染与按钮点击仍未验证；Windows 新功能须由 CI/实机验证。此前内置浏览器不可用不作为本次 UI 已通过的证据。
- 未提交或推送；未修改真实用户的插件及 hooks 配置，官方安装验证仅在隔离目录进行。


## 按标题创建 prompt 文件与本机安装（2026-11-11）

- Linux 完整回归：14 项，13 项通过，1 项 Windows 原生测试跳过。
- 测试覆盖真实标题数据库读取、非法文件名、同名任务隔离、额度失败保留、成功消费清理、提交后修改保留、正常结束空文件清理。
- 本机已从早期 /home/he/util/codex_resume_hook.py 迁移到版本化安装；内嵌插件已安装，目录为 /home/he/util/resume-prompts。
- 两个新版 hooks 已通过 Codex 官方审查界面信任，配置记录为 enabled=true；没有伪造 trusted_hash。
- 本机实际安装版本的 hook 已使用模拟 App Server 跑通：自动生成标题文件、写入追加指令、模拟额度失败及恢复、验证模型输入包含指令、完成后文件删除。不消耗真实模型额度。
- 已打开的桌面 App 需重启后加载新 hook 与插件；此前已经开始的聊天不会补登记。本轮没有重启用户正在使用的 App。


## 内嵌页面初始化超时修复（2026-11-11）

桌面日志确认 `MCP app initialization timed out`，阶段为 widget；插件工具本身能立即返回状态。页面在检测到 `window.openai.callTool` 兼容接口时，错误跳过标准 MCP Apps 初始化。修正为内嵌页面始终执行 ui/initialize，再发送 initialized，并统一通过标准 tools/call 调用工具。

新增 Node vm 前端回归夹具，模拟宿主预先注入兼容 callTool，确认标准握手和状态查询仍执行，旧兼容接口不会抢先调用。该夹具已加入 Python 测试发现；没有 Node 的开发环境会跳过此项，运行时不依赖 Node。

本机先备份 `dashboard.before-handshake-fix.html`，再热更新当前已安装资源，不改变 hook 的信任定义；重新打开控制台后观察客户端结果。


## 右侧控制台与待恢复列表（2026-11-11）

用户选择在侧边面板随时查看待恢复任务。通过桌面 open_in_codex 向当前对话右侧浏览器面板发出本地控制台打开请求，客户端返回 queued；未将此返回值宣称为已经展示或永久固定。

页面新增默认待恢复/需处理筛选（pending、waiting_quota、blocked、error），保留全部任务切换；强调恢复 prompt 可留空。前端握手及空列表回归通过。安装中的 HTML 资源已更新，不更改 hook 信任。默认筛选不改变后台执行策略，也不创建新聊天。


## 静态客户端入口（2026-11-11）

核对 OpenAI MCP Extensions 官方规范后，为 open_resume_panel 注册 `_meta["openai/ui"].entrypoints` 的 global 和 thread 两种入口，并设置中文 title。官方规范要求点击入口使用空参数调用，本工具已支持；不需要模型调用后才能显示。

本机桌面客户端静态代码包含 openai/ui.entrypoints 解析与入口注册实现，已将新版 resume_mcp.py 和页面备份后同步至当前安装目录。现有 MCP 连接需要客户端重启刷新，尚未观察到重启后的实际按钮位置。

Linux 15 项回归：14 项通过，1 项 Windows 原生测试跳过。协议回归新增对两种 entrypoints 和 title 的验证，已有空参数工具调用覆盖入口调用。未修改 hook 定义和信任状态，没有创建新聊天。

参考：https://developers.openai.com/plugins/build/extensions 和 https://github.com/openai/mcp-extensions/blob/main/docs/spec.md 。此前将“通用 MCP Apps 没有主动插入回复接口”扩大成“插件不能有静态侧栏入口”的结论不准确，后者由 OpenAI 扩展支持。
