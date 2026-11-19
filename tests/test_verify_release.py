from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_release import verify  # noqa: E402


class VerifyReleaseTests(unittest.TestCase):
    def test_valid_and_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            payload = out / "computedeck.tar.gz"
            payload.write_bytes(b"release")
            digest = hashlib.sha256(b"release").hexdigest()
            (out / "SHA256SUMS").write_text(f"{digest}  computedeck.tar.gz\n", encoding="utf-8")
            (out / "manifest.json").write_text(
                json.dumps({"product": "ComputeDeck", "version": "1.2.3", "extension_version": "1.2.3"}),
                encoding="utf-8",
            )
            self.assertTrue(verify(out)["ok"])
            payload.write_bytes(b"tampered")
            result = verify(out)
            self.assertFalse(result["ok"])
            self.assertTrue(any("校验失败" in item for item in result["problems"]))


if __name__ == "__main__":
    unittest.main()
