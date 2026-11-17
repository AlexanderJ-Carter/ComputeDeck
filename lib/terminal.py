"""交互式 SSH 终端会话（paramiko）。

供 UI ``/ws/terminal`` 使用；会话状态保存在控制面进程内。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import paramiko

from paths import expand_path, nodes_file


@dataclass
class NodeSSH:
    name: str
    host: str
    user: str
    port: int
    key: str


def load_node_ssh(name: str) -> NodeSSH:
    """从 ``nodes.json`` 解析节点 SSH 连接信息。

    :raises KeyError: 未知节点。
    :raises ValueError: 缺少 ``ansible_host``。
    :raises FileNotFoundError: SSH 私钥不存在。
    """
    doc = json.loads(nodes_file().read_text(encoding="utf-8"))
    defaults = doc.get("defaults") or {}
    nodes = doc.get("nodes") or {}
    if name not in nodes:
        raise KeyError(f"未知节点: {name}")
    meta = nodes[name]
    host = str(meta.get("ansible_host") or "")
    if not host:
        raise ValueError(f"节点 {name} 缺少 ansible_host")
    key = expand_path(
        str(meta.get("ansible_ssh_private_key_file") or defaults.get("ansible_ssh_private_key_file") or "")
    )
    if not key or not Path(key).is_file():
        raise FileNotFoundError(f"SSH 私钥不存在: {key or '(空)'}")
    return NodeSSH(
        name=name,
        host=host,
        user=str(meta.get("ansible_user") or defaults.get("ansible_user") or "root"),
        port=int(meta.get("ansible_port") or defaults.get("ansible_port") or 22),
        key=key,
    )


@dataclass
class ShellSession:
    node: NodeSSH
    client: paramiko.SSHClient
    channel: paramiko.Channel
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def write(self, data: bytes) -> None:
        """向远端 PTY 写入字节。"""
        with self.lock:
            if self.closed:
                return
            self.channel.send(data)

    def resize(self, cols: int, rows: int) -> None:
        """调整远端 PTY 行列。"""
        with self.lock:
            if self.closed:
                return
            self.channel.resize_pty(width=max(20, cols), height=max(5, rows))

    def close(self) -> None:
        """关闭 channel 与 SSH 客户端。"""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.channel.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass


def open_shell(node_name: str, *, cols: int = 120, rows: int = 32) -> ShellSession:
    """建立到指定节点的交互式 SSH shell 会话。"""
    info = load_node_ssh(node_name)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=info.host,
        port=info.port,
        username=info.user,
        key_filename=info.key,
        timeout=12,
        allow_agent=False,
        look_for_keys=False,
        banner_timeout=12,
        auth_timeout=12,
    )
    channel = client.invoke_shell(
        term="xterm-256color",
        width=cols,
        height=rows,
        width_pixels=0,
        height_pixels=0,
    )
    channel.settimeout(0.0)
    return ShellSession(node=info, client=client, channel=channel)


def pump_channel(session: ShellSession, on_data, stop_event: threading.Event) -> None:
    """循环读取 PTY 输出直至关闭。

    :param on_data: 回调 ``on_data(bytes)``。
    :param stop_event: 置位后停止泵送。
    """
    while not stop_event.is_set() and not session.closed:
        if session.channel.recv_ready():
            chunk = session.channel.recv(4096)
            if not chunk:
                break
            on_data(chunk)
            continue
        if session.channel.exit_status_ready():
            break
        time.sleep(0.02)
