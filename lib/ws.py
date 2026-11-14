"""最小 RFC6455 WebSocket 编解码（供 ThreadingHTTPServer）。"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import BinaryIO


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_key(sec_key: str) -> str:
    digest = hashlib.sha1((sec_key + GUID).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_headers(sec_key: str) -> list[tuple[str, str]]:
    return [
        ("Upgrade", "websocket"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Accept", accept_key(sec_key)),
    ]


def encode_text(message: str) -> bytes:
    payload = message.encode("utf-8")
    return _encode_frame(0x1, payload)


def encode_binary(payload: bytes) -> bytes:
    return _encode_frame(0x2, payload)


def encode_close(code: int = 1000) -> bytes:
    return _encode_frame(0x8, struct.pack("!H", code))


def encode_pong(payload: bytes = b"") -> bytes:
    return _encode_frame(0xA, payload)


def _encode_frame(opcode: int, payload: bytes) -> bytes:
    fin_opcode = 0x80 | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = bytes([fin_opcode, length])
    elif length < 65536:
        header = bytes([fin_opcode, 126]) + struct.pack("!H", length)
    else:
        header = bytes([fin_opcode, 127]) + struct.pack("!Q", length)
    return header + payload


def read_frame(sock_file: BinaryIO) -> tuple[int, bytes] | None:
    """Read one WebSocket frame. Client→server frames are masked."""
    header = sock_file.read(2)
    if not header or len(header) < 2:
        return None
    b1, b2 = header[0], header[1]
    opcode = b1 & 0x0F
    masked = (b2 & 0x80) != 0
    length = b2 & 0x7F
    if length == 126:
        ext = sock_file.read(2)
        if len(ext) < 2:
            return None
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock_file.read(8)
        if len(ext) < 8:
            return None
        length = struct.unpack("!Q", ext)[0]
    if length > 8 * 1024 * 1024:
        # Terminal frames are tiny; reject oversized frames instead of
        # allocating attacker-controlled memory.
        return None
    mask = sock_file.read(4) if masked else b""
    if masked and len(mask) < 4:
        return None
    data = sock_file.read(length) if length else b""
    if len(data) < length:
        return None
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data
