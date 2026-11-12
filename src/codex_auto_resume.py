#!/usr/bin/env python3
"""在 Codex 订阅额度恢复后继续已登记的本地任务。"""

import argparse
from resume_control import permitted, read_control, prompt_snapshot
from resume_inbox import submitted_snapshot, clean_prompt
import asyncio
from collections import deque
from datetime import datetime
from resume_platform import lock_file, unlock_file, background_options, quiet_options, codex_command, codex_home, atomic_replace
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import uuid


RECHECK_SECONDS = 30
RPC_TIMEOUT_SECONDS = 45
ACTIVE_STATES = {"pending", "running", "waiting_quota", "waiting_thread", "waiting_dsh", "dsh_action", "watching"}
DSH_RUNNER = os.environ.get("DSH_COLLAB_RUNNER")
DSH_PROJECT_RUNNER = os.environ.get("DSH_PROJECT_RUNNER")
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["complete", "continue", "blocked"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
    "additionalProperties": False,
}


class RunnerError(Exception):
    pass


class Blocked(RunnerError):
    pass


class ServerDisconnected(RunnerError):
    pass


class ThreadBusy(RunnerError):
    pass


class Job:
    def __init__(self, path, state):
        self.path = path
        self.state = state
        self.thread_lock = None
        self.rollout_offset = 0
        self.rollout_turn = None

    def claim_thread(self, thread_id):
        if self.thread_lock is not None:
            return
        # 状态文件可有多个，会话锁必须在所有执行器之间共享。
        root = codex_home() / "auto-resume-locks"
        root.mkdir(mode=0o700, exist_ok=True, parents=True)
        identity = str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve())
        name = hashlib.sha256((identity + "\n" + thread_id).encode()).hexdigest()
        lock = (root / name).open("a+b")
        try:
            lock_file(lock)
        except BlockingIOError as error:
            lock.close()
            raise RunnerError("此 Codex 会话已有续跑进程接管。") from error
        self.thread_lock = lock

    def close(self):
        if self.thread_lock is not None:
            self.thread_lock.close()

    def save(self, **changes):
        # 原子发布检查点，status 可在运行期间安全读取。
        state = {**self.state, **changes}
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            atomic_replace(temp, self.path)
            self.state = state
        finally:
            Path(temp).unlink(missing_ok=True)


class AppServer:
    def __init__(self, process):
        self.process = process
        self.request_id = 0

    async def send(self, message):
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise ServerDisconnected("Codex App Server 已断开。") from error

    async def receive(self):
        line = await self.process.stdout.readline()
        if not line:
            raise ServerDisconnected("Codex App Server 已断开，已保存的任务状态可供恢复。")
        message = json.loads(line)
        if "method" in message and "id" in message:
            # 无人值守时，权限请求和询问交回用户。
            raise Blocked(f"Codex 需要人工处理 {message['method']}")
        return message

    async def request(self, method, params, on_event=None):
        self.request_id += 1
        request_id = self.request_id

        async def exchange():
            await self.send({"id": request_id, "method": method, "params": params})
            while True:
                message = await self.receive()
                if message.get("id") == request_id:
                    if "error" in message:
                        detail = message['error']['message']
                        if method == "thread/resume" and "already has an active writer" in detail:
                            raise ThreadBusy(detail)
                        raise RunnerError(f"{method}: {detail}")
                    return message["result"]
                if on_event:
                    on_event(message)

        try:
            return await asyncio.wait_for(exchange(), RPC_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as error:
            raise RunnerError(f"{method} 超时") from error


def report(message):
    print(message, flush=True)


def latest_watched_turn(job):
    """增量读取指定聊天的根轮次事件，半行留到下次，不读其他聊天。"""
    path = Path(job.state["watch_rollout"])
    if path.stat().st_size < job.rollout_offset:
        raise Blocked("守候记录被截断，需重新核对聊天。")
    with path.open("rb") as stream:
        if job.rollout_offset == 0:
            metadata = json.loads(stream.readline())
            if (metadata.get("type") != "session_meta"
                    or metadata["payload"]["id"] != job.state["thread_id"]
                    or Path(metadata["payload"]["cwd"]).resolve() != Path(job.state["cwd"])):
                raise Blocked("守候记录的会话或工作目录不匹配。")
        else:
            stream.seek(job.rollout_offset)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                break
            event = json.loads(line)
            payload = event.get("payload", {})
            if (event.get("type") == "event_msg"
                    and payload.get("type") in ("task_started", "task_complete", "turn_aborted")
                    and payload.get("turn_id")
                    and payload.get("root_turn_id", payload["turn_id"]) == payload["turn_id"]):
                job.rollout_turn = payload
            job.rollout_offset = stream.tell()
    if job.rollout_turn is None:
        raise Blocked("守候记录中没有可识别的轮次。")
    return job.rollout_turn


def check_watched_turn(job):
    """只有指定轮次明确因额度失败才能移交；人工继续会撤销旧守候。"""
    if not job.state.get("watch_turn_id"):
        return True
    turn = latest_watched_turn(job)
    if turn["turn_id"] != job.state["watch_turn_id"]:
        job.save(status="cancelled", retry_at=None, summary="聊天已有新轮次，旧守候已撤销。")
        return False
    if turn["type"] == "task_started":
        return False
    error = turn.get("error") or {}
    if (turn["type"] == "task_complete"
            and error.get("codex_error_info") == "usage_limit_exceeded"):
        if job.state["status"] == "watching":
            job.save(status="pending", retry_at=None)
            report("指定聊天因额度中断，开始检查额度并接手续跑。")
        return True
    if turn["type"] == "task_complete" and not error:
        job.save(status="complete", summary="原聊天正常结束，未触发自动续跑。", retry_at=None)
    else:
        job.save(status="blocked", summary="原聊天被停止或发生非额度错误，不自动续跑。", retry_at=None)
    return False


def runner_alive(path):
    lock_path = Path(str(path) + ".lock")
    if not lock_path.exists():
        return False
    with lock_path.open("r+b") as stream:
        try:
            lock_file(stream)
        except BlockingIOError:
            return True
    return False


def dsh_command(job, *arguments):
    state = job.state
    project = state.get("dsh_project")
    runner = (DSH_PROJECT_RUNNER or (str(Path(DSH_RUNNER).with_name("dsh_project.py")) if DSH_RUNNER else None)) if project else DSH_RUNNER
    if not runner or not Path(runner).is_file():
        raise Blocked("DSH 为可选外部集成，请设置 DSH_COLLAB_RUNNER 为本机执行器路径。")
    process = subprocess.run(
        [sys.executable, str(runner), *arguments, "--id", project or state["dsh_job"]],
        env={**os.environ, "DSH_COLLAB_ROOT": state["dsh_root"]},
        text=True, capture_output=True, timeout=660 if arguments[0] == "check" else 30,
    )
    if process.returncode:
        raise Blocked(f"DSH 工作包操作失败。{process.stderr.strip()}")
    return json.loads(process.stdout)


def dsh_running(state, snapshot):
    return bool(snapshot.get("running_count", 0)) if state.get("dsh_project") else snapshot["status"] == "running"


def apply_dsh_action(job):
    """执行已落盘的动作，重启重放使用相同请求 ID。"""
    action = job.state["dsh_action"]
    if job.state.get("dsh_project"):
        apply_project_action(job, action)
        return
    if action["kind"] == "followup":
        task = job.path.with_name(job.path.name + ".dsh-task.md")
        task.write_text(action["text"], encoding="utf-8")
        dsh_command(job, "followup", "--task", str(task), "--request-id", action["id"])
        next_status = "waiting_dsh"
    else:
        dsh_command(job, "review", "--verdict", action["kind"],
                    "--note", action["text"], "--request-id", action["id"])
        next_status = job.state["after_dsh_action"]
        if action["kind"] == "blocked":
            next_status = "blocked"
    job.save(status=next_status, dsh_action=None)
    report(f"DSH 动作已完成 {action['kind']}")


def apply_project_action(job, action):
    """项目计划由宿主预先登记；模型只能操作其中已有的任务。"""
    kind = action["kind"]
    if kind == "followup" and action.get("task") == "assistant":
        raise Blocked("助手复核须使用 assist，followup 仅用于工程任务。")
    request = ["--request-id", action["id"]]
    if kind in ("assist", "followup"):
        task = job.path.with_name(job.path.name + ".dsh-task.md")
        task.write_text(action["text"], encoding="utf-8")
        arguments = [kind, "--task-file", str(task)]
        if kind == "followup":
            arguments += ["--task", action["task"]]
        result = dsh_command(job, *arguments, *request)
    elif kind in ("accepted", "needs-changes", "blocked"):
        arguments = ["review", "--task", action["task"], "--verdict", kind, "--note", action["text"]]
        for finding in action["resolved"]:
            arguments += ["--resolve", finding]
        result = dsh_command(job, *arguments, *request)
    elif kind == "finish":
        result = dsh_command(job, "finish", "--verdict", "accepted", "--note", action["text"], *request)
    elif kind in ("tick", "check"):
        result = dsh_command(job, kind, *request)
    else:
        raise Blocked("未知的 DSH 项目动作。")
    if job.state["after_dsh_action"] == "complete" and result["status"] != "complete":
        raise Blocked("DSH 项目尚未通过最终验收。")
    next_status = job.state["after_dsh_action"]
    if kind == "blocked":
        next_status = "blocked"
    elif result["running_count"]:
        next_status = "waiting_dsh"
    job.save(status=next_status, dsh_action=None)
    report(f"DSH 项目动作已完成 {kind}")


def output_schema(job):
    project = job.state.get("dsh_project")
    if not job.state.get("dsh_job") and not project:
        return RESULT_SCHEMA
    properties = {
        "kind": {"type": "string", "enum": ["followup", "accepted", "needs-changes", "blocked"]},
        "text": {"type": "string"},
    }
    if project:
        properties["kind"]["enum"] += ["assist", "tick", "check", "finish"]
        properties["task"] = {"type": "string"}
        properties["resolved"] = {"type": "array", "items": {"type": "string"}}
    return {
        **RESULT_SCHEMA,
        "properties": {
            **RESULT_SCHEMA["properties"],
            "status": {"type": "string", "enum": ["complete", "continue", "blocked", "wait_dsh"]},
            "dsh_action": {"anyOf": [
                {"type": "null"},
                {"type": "object", "properties": properties,
                 "required": list(properties), "additionalProperties": False},
            ]},
        },
        "required": ["status", "summary", "dsh_action"],
    }


def quota_deadline(result):
    """返回最晚解除的已用尽窗口，零表示可以提交。"""
    buckets = result.get("rateLimitsByLimitId") or {}
    limits = buckets.get("codex") or result["rateLimits"]
    if limits.get("limitId") not in (None, "codex"):
        raise Blocked("额度响应缺少 Codex 主额度。")
    reason = limits.get("rateLimitReachedType")
    if reason not in (None, "rate_limit_reached"):
        raise Blocked(f"账户额度需要人工处理（{reason}）。")

    windows = [limits[key] for key in ("primary", "secondary") if limits.get(key)]
    if not windows:
        raise Blocked("额度响应没有可用的 Codex 窗口。")
    exhausted = [window for window in windows if window["usedPercent"] >= 100]
    if not exhausted and reason is None:
        return 0
    latest_reset = max((window.get("resetsAt") or 0 for window in exhausted), default=0)
    now = time.time()
    return latest_reset if latest_reset > now else now + RECHECK_SECONDS


def wait_for_quota(job, deadline):
    job.save(status="waiting_quota", retry_at=deadline, turn_id=None)
    local_time = datetime.fromtimestamp(deadline).astimezone().isoformat(timespec="seconds")
    report(f"等待 Codex 额度，下次查询 {local_time}")


def settle_turn(job, turn, answer):
    """只有额度失败可自动重试，任务完成需要模型明确提交验收结果。"""
    error = turn.get("error") or {}
    if turn["status"] == "failed" and error.get("codexErrorInfo") == "usageLimitExceeded":
        job.save(status="waiting_quota", retry_at=None, turn_id=None)
        report("当前轮次遇到 Codex 额度限制，正在查询重置时间。")
        return
    if turn["status"] != "completed":
        raise Blocked(error.get("message") or f"轮次状态为 {turn['status']}")

    clean_prompt(job, consumed=True)
    try:
        result = json.loads(answer or "")
    except json.JSONDecodeError as error:
        raise Blocked("轮次结束，但缺少可解析的任务验收结果。") from error
    if (
        not isinstance(result, dict)
        or result.get("status") not in ("complete", "continue", "blocked", "wait_dsh")
        or not isinstance(result.get("summary"), str)
        or not result["summary"].strip()
    ):
        raise Blocked("轮次结束，但任务验收结果不符合约定。")
    summary = result["summary"].strip()
    status = result["status"]
    if job.state.get("dsh_project"):
        settle_project_turn(job, result)
        return
    if job.state.get("dsh_job"):
        worker = dsh_command(job, "status")
        action = result.get("dsh_action")
        if action is not None:
            if (
                not isinstance(action, dict)
                or action.get("kind") not in ("followup", "accepted", "needs-changes", "blocked")
                or not isinstance(action.get("text"), str) or not action["text"].strip()
                or (status == "complete" and action["kind"] != "accepted")
            ):
                raise Blocked("DSH 动作与任务状态不一致。")
            job.save(status="dsh_action", summary=summary, turn_id=None,
                     dsh_action={**action, "id": str(uuid.uuid4())},
                     after_dsh_action="complete" if status == "complete" else "pending")
            return
        if status == "complete" and (worker.get("review") or {}).get("verdict") != "accepted":
            raise Blocked("DSH 工作包尚未验收，任务不能标记完成。")
        if status in ("continue", "wait_dsh") and worker["status"] == "running":
            job.save(status="waiting_dsh", summary=summary, turn_id=None)
            return
    if status == "wait_dsh":
        if not job.state.get("dsh_job"):
            raise Blocked("此任务没有绑定 DSH 工作包。")
        status = "continue"
    if status == "continue" and summary == job.state.get("summary"):
        raise Blocked(f"连续两轮报告相同进度，需要检查任务卡点。\n{summary}")
    job.save(status="pending" if status == "continue" else status, summary=summary, turn_id=None)
    report(f"{status}: {summary}")


def settle_project_turn(job, result):
    current = dsh_command(job, "status")
    action = result.get("dsh_action")
    status = result["status"]
    summary = result["summary"].strip()
    if action is not None:
        if (not isinstance(action, dict)
                or action.get("kind") not in ("assist", "followup", "tick", "check", "finish", "accepted", "needs-changes", "blocked")
                or not isinstance(action.get("text"), str) or not action["text"].strip()
                or not isinstance(action.get("task"), str)
                or not isinstance(action.get("resolved"), list)
                or any(not isinstance(item, str) for item in action["resolved"])
                or (status == "complete" and action["kind"] != "finish")):
            raise Blocked("DSH 项目动作与任务状态不一致。")
        if action["kind"] in ("followup", "accepted", "needs-changes", "blocked") and action["task"] not in ["assistant", *[row["id"] for row in current["tasks"]]]:
            raise Blocked("DSH 项目动作引用了未登记的任务。")
        if action["kind"] == "followup" and action["task"] == "assistant":
            raise Blocked("助手复核须使用 assist，followup 仅用于工程任务。")
        job.save(status="dsh_action", summary=summary, turn_id=None,
                 dsh_action={**action, "id": str(uuid.uuid4())},
                 after_dsh_action="complete" if status == "complete" else "pending")
        return
    if status == "complete" and current["status"] != "complete":
        raise Blocked("DSH 项目尚未通过最终验收。")
    if status in ("continue", "wait_dsh") and current["running_count"]:
        job.save(status="waiting_dsh", summary=summary, turn_id=None)
        return
    if status in ("continue", "wait_dsh") and summary == job.state.get("summary"):
        raise Blocked(f"连续两轮报告相同进度，需要检查任务卡点。\n{summary}")
    job.save(status="pending" if status in ("continue", "wait_dsh") else status, summary=summary, turn_id=None)


async def recover_turn(server, job):
    # 确认上次的实际结局，避免在进程崩溃后重发可能已执行的指令。
    state = job.state
    if not state.get("turn_id"):
        raise Blocked("上次在提交轮次时中断，需查看原会话确认是否已执行。")
    thread = (await server.request(
        "thread/read", {"threadId": state["thread_id"], "includeTurns": True}
    ))["thread"]
    turn = next((turn for turn in thread["turns"] if turn["id"] == state["turn_id"]), None)
    if turn is None or turn["status"] == "inProgress":
        raise Blocked("上次轮次的结局无法确认，需检查原会话和工作目录。")
    answers = [
        item["text"] for item in turn["items"]
        if item["type"] == "agentMessage" and item.get("phase") != "commentary"
    ]
    settle_turn(job, turn, answers[-1] if answers else None)


async def run_turn(server, job):
    # 额度等待或连接握手期间，用户可能已经手动继续。
    if not check_watched_turn(job):
        return
    if not permitted(job.path, job.state.get("applied_control_id")):
        return
    control = read_control(job.path)
    state = job.state
    thread_id = state["thread_id"]
    sandbox = {
        "type": "workspaceWrite" if state["workspace_write"] else "readOnly",
        "networkAccess": state["allow_network"],
    }
    if state["workspace_write"]:
        sandbox["writableRoots"] = [state["cwd"]]

    # 每轮先核对已落盘的成果和工程师状态，再从仍未完成的工作继续。
    prompt = (
        f"{state['prompt']}\n\n"
        "这是一个可恢复任务。先检查当前文件和既有运行记录，已完成的步骤不要重跑。"
        "若已有 dsh 工作包，先查其会话、进程或产物，再决定是否需要派工。"
        "依据实际证据返回状态和摘要。complete 表示全部验收条件满足。"
        "continue 表示本轮有可验证进展且还有下一步，摘要写明进展和下一步。"
        "blocked 表示需要人工决策、授权或外部条件变化。"
    )
    if state.get("summary"):
        prompt += f"\n上一轮检查点\n{state['summary']}"
    if state.get("dsh_job"):
        worker = dsh_command(job, "status")
        prompt += (
            "\n你负责验收这个已授权工作包。执行器在宿主机代办下面的动作。"
            "请通过最终 JSON 的 dsh_action 请求返工或记录验收，不要自行运行派工或 review 命令。"
            "dsh_action 为 null 表示本轮无需动作。"
            "返工使用 {kind: followup, text: 完整的返工任务单}。"
            "验收使用 {kind: accepted/needs-changes/blocked, text: 实际验证证据和结论}。"
            "等待执行使用 status=wait_dsh。只有验收通过才用 complete。"
            "实际检查工作目录及测试结果，不以工程师自述代替验证。"
            "\n当前工作包\n" + json.dumps(worker, ensure_ascii=False)
        )
    if state.get("dsh_project"):
        project = dsh_command(job, "status")
        prompt += (
            "\n你负责这个已登记 DSH 项目的最终验收。助手发现是建议，须核对实际文件和测试证据。"
            "只通过最终 JSON 的 dsh_action 请求宿主执行动作，不自行启动/验收工作包。"
            "动作字段为 kind、text、task、resolved；无需指定任务时 task=空字符串，无处置时 resolved=[]。"
            "kind=assist 请求助手复核当前证据；accepted/needs-changes/blocked 记录 task 的验收；"
            "followup 为 task 提交完整返工单；tick 启动计划内依赖就绪任务；check 执行登记的集成检查；"
            "finish 完成项目验收。重大助手发现须解决后复核或在 resolved 中列 ID 并用 text 写处置证据。"
            "dsh_action=null 表示无动作，等待运行用 wait_dsh。只有项目全部通过才能 complete。"
            "禁止扩大已登记范围；写任务的集成基线需人工/主 agent 核对后绑定。"
            "\n当前项目\n" + json.dumps(project, ensure_ascii=False)
        )

    try:
        extra, prompt_id = prompt_snapshot(control)
    except (OSError, ValueError) as error:
        raise Blocked(f"无法读取恢复 prompt 文件：{error}") from error
    if prompt_id != state.get('applied_prompt_id') and extra.strip():
        prompt += "\n用户追加的恢复指令（遵守原授权边界）：\n" + extra
    events = deque()

    def keep_event(message):
        if (
            message.get("method") in ("item/completed", "turn/completed")
            and message["params"]["threadId"] == thread_id
        ):
            events.append(message)

    job.save(status="running", turn_id=None, retry_at=None, applied_control_id=control["revision"], applied_prompt_id=prompt_id,
             inbox_submission=submitted_snapshot(state,control,extra))
    started = await server.request("turn/start", {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
        "approvalPolicy": "never",
        "sandboxPolicy": sandbox,
        "outputSchema": output_schema(job),
        **({"model": state["model"]} if state.get("model") else {}),
    }, on_event=keep_event)
    turn_id = started["turn"]["id"]
    job.save(turn_id=turn_id, watch_turn_id=None)
    report(f"执行轮次 {turn_id}")
    answer = None
    while True:
        if not events:
            keep_event(await server.receive())
            continue
        message = events.popleft()
        params = message["params"]
        if message["method"] == "item/completed" and params["turnId"] == turn_id:
            item = params["item"]
            if item["type"] == "agentMessage" and item.get("phase") != "commentary":
                answer = item["text"]
        elif message["method"] == "turn/completed" and params["turn"]["id"] == turn_id:
            settle_turn(job, params["turn"], answer)
            return


async def drive(server, job):
    await server.request("initialize", {
        "clientInfo": {"name": "codex_auto_resume", "version": "1.0.0"},
    })
    await server.send({"method": "initialized"})
    account = await server.request("account/read", {"refreshToken": False})
    if (account.get("account") or {}).get("type") != "chatgpt":
        raise Blocked("请先在桌面 App 或本机 Codex CLI 中登录同一 ChatGPT 账户。")

    if job.state["status"] == "running":
        await recover_turn(server, job)
    loaded = False
    while job.state["status"] in {"pending", "running", "waiting_quota", "waiting_thread"}:
        if not check_watched_turn(job) or not permitted(job.path, job.state.get("applied_control_id")):
            return
        try:
            limits = await server.request("account/rateLimits/read", {})
        except (Blocked, ServerDisconnected):
            raise
        except RunnerError as error:
            if job.state["status"] != "waiting_quota":
                raise
            report(f"额度查询暂时失败，稍后复查。{error}")
            job.save(retry_at=time.time() + RECHECK_SECONDS)
            return
        deadline = quota_deadline(limits)
        if not deadline and job.state["status"] == "waiting_quota" and job.state["retry_at"] is None:
            # 失败事件可能早于额度快照更新，首次出现矛盾时复查一次。
            deadline = time.time() + RECHECK_SECONDS
        if deadline:
            wait_for_quota(job, deadline)
            return

        if not loaded:
            thread_id = job.state.get("thread_id")
            if thread_id:
                existing = (await server.request("thread/read", {"threadId": thread_id}))["thread"]
                if Path(existing["cwd"]).resolve() != Path(job.state["cwd"]):
                    raise Blocked("现有会话的工作目录与登记目录不一致。")
                if existing["status"]["type"] == "active":
                    raise Blocked("现有会话正在执行，需先确认其结果。")
                await server.request("thread/resume", {
                    "threadId": thread_id, "approvalPolicy": "never",
                    "sandbox": "workspace-write" if job.state["workspace_write"] else "read-only",
                    **({"model": job.state["model"]} if job.state.get("model") else {}),
                })
            else:
                result = await server.request("thread/start", {
                    "cwd": job.state["cwd"],
                    "approvalPolicy": "never",
                    "sandbox": "workspace-write" if job.state["workspace_write"] else "read-only",
                    **({"model": job.state["model"]} if job.state.get("model") else {}),
                })
                thread_id = result["thread"]["id"]
                job.claim_thread(thread_id)
                job.save(thread_id=thread_id)
            report(f"Codex 会话 {thread_id}")
            loaded = True
        await run_turn(server, job)


async def drive_once(job):
    process = await asyncio.create_subprocess_exec(
        *codex_command(), "app-server", "--listen", "stdio://",
        **quiet_options(),
        env={**os.environ, "CODEX_AUTO_RESUME_CHILD": "1"},
        cwd=job.state["cwd"], stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
    )
    try:
        await drive(AppServer(process), job)
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.communicate()


async def run(job):
    # 等待期间只保留本地计时器，到点再启动 App Server 复查额度。
    while job.state["status"] in ACTIVE_STATES:
        try:
            if not check_watched_turn(job):
                if job.state["status"] == "watching":
                    await asyncio.sleep(2)
                continue
            # 已提交轮次的结果核对不能被新的暂停设置阻止；暂停只控制后续提交。
            if job.state["status"] != "running" and not permitted(job.path, job.state.get("applied_control_id")):
                await asyncio.sleep(2)
                continue
            if (delay := (job.state.get("retry_at") or 0) - time.time()) > 0:
                await asyncio.sleep(min(delay, 2))
                continue
            if job.state["status"] == "dsh_action":
                apply_dsh_action(job)
                continue
            if job.state["status"] == "waiting_dsh":
                worker = dsh_command(job, "status")
                if dsh_running(job.state, worker):
                    await asyncio.sleep(2)
                    continue
                job.save(status="pending")
            if (job.state.get("dsh_job") or job.state.get("dsh_project")) and job.state["status"] == "pending":
                worker = dsh_command(job, "status")
                if dsh_running(job.state, worker):
                    job.save(status="waiting_dsh")
                    continue
            await drive_once(job)
        except Blocked as error:
            job.save(status="blocked", summary=str(error), retry_at=None)
            report(f"blocked: {error}")
        except ThreadBusy as error:
            message = "原客户端仍持有聊天写锁；等待其释放后自动重试。可关闭该聊天或退出原客户端，勿同时手动续跑。"
            job.save(status="waiting_thread", retry_at=time.time() + RECHECK_SECONDS, summary=message)
            report(f"{message} {error}")
        except ServerDisconnected as error:
            if job.state["status"] in ("waiting_quota", "waiting_thread"):
                report(f"恢复连接暂时失败，稍后复查。{error}")
                job.save(retry_at=time.time() + RECHECK_SECONDS)
            else:
                job.save(status="blocked", summary=str(error), retry_at=None)
                report(f"blocked: {error}")
        except RunnerError as error:
            job.save(status="blocked", summary=str(error), retry_at=None)
            report(f"blocked: {error}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="同一工作任务始终使用同一个状态文件，执行时让脚本独占该 Codex 会话。"
        "遇到 blocked 时，先人工检查原会话，再用新状态文件和 --thread-id 登记剩余工作。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run", help="登记新任务或恢复已有任务")
    status = commands.add_parser("status", help="查看任务状态，不启动 Codex")
    for command in (execute, status):
        command.add_argument("--state", type=Path, required=True, help="任务状态 JSON 文件")
    execute.add_argument("--task", type=Path, help="首次登记时读取的 UTF-8 任务单")
    execute.add_argument("--cwd", type=Path, help="首次登记时指定的工作目录")
    execute.add_argument("--thread-id", help="首次登记时续接指定的本地 Codex 会话")
    execute.add_argument("--workspace-write", action="store_true", help="允许在工作区写入，默认只读")
    execute.add_argument("--allow-network", action="store_true", help="允许任务工具访问网络")
    execute.add_argument("--model", help="首次登记时指定本账户可用的 Codex 模型")
    execute.add_argument("--dsh-job", help="绑定已登记的单个 DSH 工作包")
    execute.add_argument("--dsh-project", help="绑定已登记的 DSH 项目（与 --dsh-job 互斥）")
    execute.add_argument("--dsh-root", type=Path, help="工作包记录目录")
    execute.add_argument("--watch-rollout", type=Path,
                         help="守候 --thread-id 的本地记录，仅在当前轮次因额度失败后接手")
    execute.add_argument("--background", action="store_true", help="独立后台运行，日志写入状态文件旁的 .log")
    args = parser.parse_args()
    path = args.state.expanduser().resolve()

    try:
        if args.command == "status":
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
                print(json.dumps({**{k: v for k, v in state.items() if k != "prompt"},
                                  "runner_alive": runner_alive(path)},
                                 ensure_ascii=False, indent=2))
            else:
                report("没有登记任务。")
            return 0

        if not path.exists() and args.task is None:
            report("没有登记任务。")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(lock, "r+"):
            try:
                lock_file(lock)
            except BlockingIOError:
                report("此任务已有续跑进程。")
                return 0

            if path.exists():
                if any((args.task, args.cwd, args.thread_id, args.workspace_write, args.allow_network,
                        args.model, args.dsh_job, args.dsh_project, args.dsh_root, args.watch_rollout)):
                    raise RunnerError("任务已登记，恢复时只传 --state。")
                job = Job(path, json.loads(path.read_text(encoding="utf-8")))
            else:
                if not args.cwd or not args.cwd.is_dir():
                    raise RunnerError("首次登记需要通过 --cwd 指定一个存在的目录。")
                prompt = args.task.read_text(encoding="utf-8").strip()
                if not prompt:
                    raise RunnerError("任务单为空。")
                if args.dsh_job and args.dsh_project:
                    raise RunnerError("--dsh-job 与 --dsh-project 不能同时指定。")
                if args.dsh_root and not (args.dsh_job or args.dsh_project):
                    raise RunnerError("--dsh-root 需要同时指定 --dsh-job 或 --dsh-project。")
                job = Job(path, {})
                dsh_root = str((args.dsh_root or Path(os.environ.get("DSH_COLLAB_ROOT")
                               or Path.home() / ".local/state/dsh-collab")).expanduser().resolve())
                if args.dsh_job:
                    dsh_command(Job(path, {"dsh_job": args.dsh_job, "dsh_root": dsh_root}), "status")
                if args.dsh_project:
                    current = dsh_command(Job(path, {"dsh_project": args.dsh_project, "dsh_root": dsh_root}), "status")
                    if Path(current["cwd"]).resolve() != args.cwd.resolve():
                        raise RunnerError("绑定的 DSH 项目目录与 --cwd 不一致。")
                initial = {}
                if args.watch_rollout:
                    if not args.thread_id:
                        raise RunnerError("--watch-rollout 必须指定 --thread-id。")
                    probe = Job(path, {"watch_rollout": str(args.watch_rollout.expanduser().resolve()),
                                      "thread_id": args.thread_id, "cwd": str(args.cwd.resolve())})
                    turn = latest_watched_turn(probe)
                    initial = {"watch_rollout": probe.state["watch_rollout"], "watch_turn_id": turn["turn_id"]}
                job.save(status="watching" if initial else "pending", prompt=prompt, cwd=str(args.cwd.resolve()),
                         thread_id=args.thread_id, workspace_write=args.workspace_write,
                         allow_network=args.allow_network, model=args.model,
                         dsh_job=args.dsh_job, dsh_project=args.dsh_project, dsh_root=dsh_root, **initial)

            if job.state["status"] in ACTIVE_STATES:
                if args.background:
                    # 子进程独立于调用者会话；返回前确认其已持有执行锁。
                    unlock_file(lock)
                    log_path = Path(str(path) + ".log")
                    with log_path.open("ab") as log:
                        process = subprocess.Popen(
                            [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "run", "--state", str(path)],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            **background_options(),
                        )
                    for _ in range(50):
                        if runner_alive(path):
                            report(f"后台执行器已启动，PID {process.pid}，日志 {log_path}")
                            return 0
                        if process.poll() is not None:
                            report(f"后台执行器已退出（{process.returncode}），请查看 {log_path}")
                            return process.returncode
                        time.sleep(0.1)
                    raise RunnerError(f"未确认后台执行锁，请查看 {log_path}")
                try:
                    if job.state.get("thread_id"):
                        job.claim_thread(job.state["thread_id"])
                    asyncio.run(run(job))
                finally:
                    job.close()
            else:
                report(f"没有可自动续跑的任务（{job.state['status']}）。")
                return 0
            return 0 if job.state["status"] == "complete" else 1
    except KeyboardInterrupt:
        report("已停止，任务状态已保留。")
        return 130
    except (OSError, RunnerError, ValueError, KeyError) as error:
        print(f"codex_auto_resume: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
