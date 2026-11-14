from __future__ import annotations

import io
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from ws import accept_key, encode_text, read_frame  # noqa: E402


def masked_frame(payload: bytes, *, opcode: int = 0x1) -> bytes:
    mask = b"\x01\x02\x03\x04"
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if len(payload) < 126:
        header = bytes([0x80 | opcode, 0x80 | len(payload)])
    else:
        header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", len(payload))
    return header + mask + data


class WsTests(unittest.TestCase):
    def test_rfc_accept_key(self) -> None:
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_read_masked_text_frame(self) -> None:
        frame = masked_frame("hello 世界".encode())
        opcode, data = read_frame(io.BytesIO(frame))
        self.assertEqual(opcode, 0x1)
        self.assertEqual(data.decode(), "hello 世界")

    def test_read_medium_frame(self) -> None:
        payload = b"x" * 200
        frame = masked_frame(payload)
        opcode, data = read_frame(io.BytesIO(frame))
        self.assertEqual(opcode, 0x1)
        self.assertEqual(data, payload)

    def test_empty_input(self) -> None:
        self.assertIsNone(read_frame(io.BytesIO(b"")))

    def test_encode_text_header(self) -> None:
        raw = encode_text("hi")
        self.assertEqual(raw[0], 0x81)
        self.assertEqual(raw[1], 2)
        self.assertEqual(raw[2:], b"hi")


if __name__ == "__main__":
    unittest.main()
