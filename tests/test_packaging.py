# -*- coding: utf-8 -*-
"""打包脚本的纯函数测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import package  # noqa: E402


class ChecksumParsingTests(unittest.TestCase):
    SHA = "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5"

    def test_plain_sha256_file(self):
        self.assertEqual(package._parse_sha256(f"{self.SHA}  archive.zip\n"), self.SHA)

    def test_certutil_output(self):
        text = (
            "SHA256 hash of ripgrep-15.2.0-x86_64-pc-windows-msvc.zip:\n"
            "71B2FEF860ABE467217A538FF31DE02F5258807C0129F771846F87BD029AAFC5\n"
            "CertUtil: -hashfile command completed successfully.\n"
        )
        self.assertEqual(package._parse_sha256(text), self.SHA)

    def test_invalid_checksum_file(self):
        self.assertEqual(package._parse_sha256("not a checksum"), "")


if __name__ == "__main__":
    unittest.main()
