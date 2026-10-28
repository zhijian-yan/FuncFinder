# -*- coding: utf-8 -*-
"""打包脚本的纯函数测试。"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

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


class GlibcCompatibilityTests(unittest.TestCase):
    def test_version_tuple_normalizes_components(self):
        self.assertEqual(package._version_tuple("2.35"), (2, 35, 0))
        self.assertEqual(package._version_tuple("2.43.1"), (2, 43, 1))

    def test_newer_host_glibc_is_rejected(self):
        with mock.patch(
            "package.platform.libc_ver", return_value=("glibc", "2.43")
        ), mock.patch("package.log"):
            with self.assertRaises(package.PackagingError):
                package.ensure_host_glibc_compatible("2.35")

    def test_same_or_older_host_glibc_is_accepted(self):
        for version in ("2.35", "2.31"):
            with mock.patch(
                "package.platform.libc_ver",
                return_value=("glibc", version),
            ), mock.patch("package.log"):
                package.ensure_host_glibc_compatible("2.35")

    def test_unknown_libc_skips_check(self):
        with mock.patch(
            "package.platform.libc_ver", return_value=("", "")
        ), mock.patch("package.log"):
            package.ensure_host_glibc_compatible("2.35")

    def test_invalid_limit_is_rejected(self):
        with mock.patch("package.log"):
            with self.assertRaises(package.PackagingError):
                package.ensure_host_glibc_compatible("abc")


class DockerOptionTests(unittest.TestCase):
    def test_default_image(self):
        args = package.parse_arguments(["--docker"])
        self.assertEqual(args.docker, package.DEFAULT_DOCKER_IMAGE)

    def test_docker_rejects_host_only_options(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(
            io.StringIO()
        ):
            package.parse_arguments(["--docker", "--no-venv"])


if __name__ == "__main__":
    unittest.main()
