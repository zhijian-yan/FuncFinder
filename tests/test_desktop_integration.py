# -*- coding: utf-8 -*-
"""桌面集成逻辑的单元测试（不需要显示器）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import func_finder  # noqa: E402  (导入需要先设置 sys.path)


class DesktopIntegrationTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(func_finder.__version__, "0.4.0")

    def test_entry_text_quotes_exec_and_sets_dock_keys(self):
        text = func_finder._desktop_entry_text(["/tmp/My App/FuncFinder"])

        self.assertIn("[Desktop Entry]", text)
        self.assertIn('Exec="/tmp/My App/FuncFinder"', text)
        self.assertIn("Icon=func-finder", text)
        self.assertIn("StartupWMClass=FuncFinder", text)
        self.assertIn("Categories=Development;", text)
        self.assertTrue(text.endswith("\n"))

    def test_install_is_idempotent_and_uninstall_removes_files(self):
        icon = ROOT / "assets" / "func_finder.png"
        self.assertTrue(icon.is_file())

        with tempfile.TemporaryDirectory() as temporary:
            data_home = Path(temporary) / "share"
            with mock.patch.object(func_finder, "_desktop_refresh_database"):
                changed = func_finder._desktop_install(
                    ["/opt/FuncFinder/FuncFinder"],
                    icon,
                    data_home,
                )
                self.assertTrue(changed)

                desktop_file = (
                    data_home / "applications" / "func-finder.desktop"
                )
                icon_512 = (
                    data_home
                    / "icons"
                    / "hicolor"
                    / "512x512"
                    / "apps"
                    / "func-finder.png"
                )
                icon_192 = (
                    data_home
                    / "icons"
                    / "hicolor"
                    / "192x192"
                    / "apps"
                    / "func-finder.png"
                )
                self.assertTrue(desktop_file.is_file())
                self.assertTrue(icon_512.is_file())
                self.assertTrue(icon_192.is_file())

                changed_again = func_finder._desktop_install(
                    ["/opt/FuncFinder/FuncFinder"],
                    icon,
                    data_home,
                )
                self.assertFalse(changed_again)

                removed = func_finder._desktop_uninstall(data_home)

            self.assertEqual(len(removed), 3)
            self.assertFalse(desktop_file.exists())
            self.assertFalse(icon_512.exists())
            self.assertFalse(icon_192.exists())


if __name__ == "__main__":
    unittest.main()
