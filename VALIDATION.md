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
