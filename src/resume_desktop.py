"""经桌面 App Tools 管道请求持锁方卸载闲置聊天，不删除锁文件。"""
import json
import os
import socket
import sqlite3
import struct
import sys
import uuid
from pathlib import Path
from resume_platform import codex_home


class DesktopError(RuntimeError):
    pass


class ThreadChanged(DesktopError):
    pass


class RestoreUnconfirmed(DesktopError):
    pass


class DesktopBridge:
    def __init__(self, thread_id, turn_id, endpoint=None):
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.endpoint = endpoint or os.environ.get('CODEX_APP_TOOLS_PIPE_PATH')
        if not self.endpoint:
            raise DesktopError('当前执行器未获得桌面控制管道，不能自动释放其他客户端的锁。')

    def request(self, method, params):
        packet = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
        if os.name == 'nt':
            channel = open(self.endpoint, 'r+b', buffering=0)
            connection = None
        else:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(20)
            try:
                connection.connect(self.endpoint)
                channel = connection.makefile('rwb', buffering=0)
            except BaseException:
                connection.close()
                raise
        try:
            with channel:
                frame = memoryview(struct.pack('<I', len(packet)) + packet)
                while frame:
                    written = channel.write(frame)
                    if not written:
                        raise DesktopError('桌面控制请求未完整写入。')
                    frame = frame[written:]
                def exact(size):
                    result = bytearray()
                    while len(result) < size:
                        part = channel.read(size - len(result))
                        if not part:
                            raise DesktopError('桌面控制连接已关闭。')
                        result.extend(part)
                    return result
                size = struct.unpack('<I', exact(4))[0]
                if not 0 < size <= 8 * 1024 * 1024:
                    raise DesktopError('桌面控制返回了无效帧长度。')
                result = json.loads(exact(size))
        finally:
            if connection is not None:
                connection.close()
        if not isinstance(result,dict) or result.get('jsonrpc') != '2.0' or result.get('id') != 1:
            raise DesktopError('桌面控制响应格式无效。')
        if 'error' in result:
            error = result['error']
            raise DesktopError(error.get('message','桌面控制请求失败。') if isinstance(error,dict) else '桌面控制请求失败。')
        if not isinstance(result.get('result'),dict):
            raise DesktopError('桌面控制未返回有效结果。')
        return result['result']

    def call(self, tool, arguments):
        result = self.request('tools/call', {
            'namespace':'codex_app','tool':tool,'arguments':arguments,
            'callerSource':'codex','threadId':self.thread_id,'turnId':self.turn_id,
            'callId':'mcp-auto-resume-' + str(uuid.uuid4())})
        items=result.get('contentItems',[])
        if not isinstance(items,list) or any(not isinstance(item,dict) for item in items):
            raise DesktopError('桌面工具返回格式无效。')
        if not result.get('success'):
            message = next((item.get('text','') for item in items
                            if item.get('type') == 'inputText'), '桌面工具拒绝请求。')
            raise DesktopError(message)
        return result

    def idle_snapshot(self):
        # wait_threads 禁止等待调用者自己；读取本线程最近一轮元数据。
        result = self.call('read_thread', {'threadId':self.thread_id,'hostId':'local',
                                          'turnLimit':1,'includeOutputs':False,'maxOutputCharsPerItem':1})
        text = next((item['text'] for item in result.get('contentItems',[])
                     if item.get('type') == 'inputText'), '{}')
        data = json.loads(text)
        if not isinstance(data,dict):
            raise DesktopError('桌面聊天元数据格式无效。')
        thread = data.get('thread') or {}
        turns = data.get('turns') or []
        turn = turns[0] if len(turns) == 1 else {}
        if not isinstance(thread,dict) or not isinstance(turn,dict) or not isinstance(thread.get('status'),dict):
            raise DesktopError('桌面聊天状态格式无效。')
        if (thread.get('id') != self.thread_id or turn.get('id') != self.turn_id
                or thread.get('status',{}).get('type') == 'active'
                or turn.get('status') not in ('completed','failed')):
            raise ThreadChanged('聊天已开始新轮次、仍在执行或未由该桌面加载；本次不释放锁。')
        if thread.get('status',{}).get('type') not in ('idle','systemError'):
            raise DesktopError('该桌面未加载此聊天，不能替其他客户端释放写锁。')
        return {'thread':thread,'latestTurn':turn}

    def ensure_no_native_children(self):
        # 原生归档可能递归归档 Codex 子会话；DSH 会话不在此关系表内。
        databases=sorted(codex_home().glob('state_*.sqlite'),reverse=True)
        if not databases:
            raise DesktopError('无法核对原生子会话，未执行自动归档。')
        try:
            db=sqlite3.connect(databases[0].as_uri()+'?mode=ro',uri=True,timeout=2)
            try:
                child=db.execute('SELECT 1 FROM thread_spawn_edges WHERE parent_thread_id=? LIMIT 1',
                                 (self.thread_id,)).fetchone()
            finally:db.close()
        except sqlite3.Error as error:
            raise DesktopError('无法核对子会话关系，未执行自动归档。') from error
        if child:
            raise DesktopError('此聊天有原生 Codex 子会话，自动释放暂不操作，避免改变子会话的归档状态。')


def release_writer(thread_id, turn_id, bridge=None):
    if not isinstance(thread_id,str) or not thread_id or not isinstance(turn_id,str) or not turn_id:
        raise DesktopError('自动释放写锁需要已登记的原聊天和轮次标识。')
    client = bridge or DesktopBridge(thread_id, turn_id)
    # 仅操作确切的已结束轮次。归档会卸载 owner，立即还原保留原 ID 和历史。
    client.idle_snapshot()
    client.ensure_no_native_children()
    client.idle_snapshot()
    args = {'threadId':thread_id,'hostId':'local','source':'codex'}
    archived = False
    try:
        client.call('set_thread_archived', {**args,'archived':True})
        archived = True
    finally:
        # 请求超时也可能已生效，因此始终尝试还原，失败须明确交回人工。
        try:
            client.call('set_thread_archived', {**args,'archived':False})
        except (DesktopError,OSError,ValueError) as error:
            raise RestoreUnconfirmed('临时归档后的聊天还原未确认；停止续跑，请检查已归档聊天。') from error
    if not archived:
        raise DesktopError('未确认桌面已经卸载聊天，未启动续跑。')
    return {'thread_id':thread_id,'turn_id':turn_id,'released':True,'restored':True}


def main():
    data={}
    try:
        data=json.load(sys.stdin)
        if data.get('restore_only'):
            client=DesktopBridge(data['thread_id'],data['turn_id'])
            client.call('set_thread_archived',{'threadId':data['thread_id'],'hostId':'local','source':'codex','archived':False})
            result={'restored':True}
        else:
            result=release_writer(data['thread_id'],data['turn_id'])
        print(json.dumps(result,ensure_ascii=False));return 0
    except (DesktopError,OSError,ValueError,KeyError,TypeError) as error:
        message = f'桌面控制通道不可用（{type(error).__name__}）。' if isinstance(error,OSError) else str(error)
        print(json.dumps({'error':message,'changed':isinstance(error,ThreadChanged),
                          'needs_restore':bool(data.get('restore_only')) or isinstance(error,RestoreUnconfirmed)},ensure_ascii=False))
        return 1


if __name__=='__main__':
    sys.exit(main())
