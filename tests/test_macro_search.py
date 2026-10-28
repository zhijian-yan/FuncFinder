"""宏定义查询的纯逻辑测试（不需要显示器）。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import func_finder  # noqa: E402  (导入需要先设置 sys.path)


def extract(source, name, language):
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if name not in line:
            continue
        found = func_finder.extract_macro(lines, index, name, language)
        if found:
            return found
    return None


class MacroExtractionTests(unittest.TestCase):
    SOURCE = (
        "#define MAX_SIZE 100\n"
        "#define MIN(a, b) ((a) < (b) ? (a) : (b))\n"
        "#define SUM(a, b) \\\n"
        "    ((a) + \\\n"
        "     (b))\n"
        "int value = MAX_SIZE;\n"
        "/* #define HIDDEN_VALUE 1 */\n"
    )

    def test_object_like_macro(self):
        self.assertEqual(
            extract(self.SOURCE, "MAX_SIZE", "C/C++"),
            ("#define MAX_SIZE", "#define MAX_SIZE 100", 1, 1),
        )

    def test_function_like_macro(self):
        signature, implementation, start, end = extract(
            self.SOURCE, "MIN", "C/C++"
        )
        self.assertEqual(signature, "#define MIN(a, b)")
        self.assertIn("#define MIN(a, b)", implementation)
        self.assertEqual((start, end), (2, 2))

    def test_multiline_macro(self):
        signature, implementation, start, end = extract(
            self.SOURCE, "SUM", "C/C++"
        )
        self.assertEqual(signature, "#define SUM(a, b)")
        self.assertEqual((start, end), (3, 5))
        self.assertEqual(implementation.count("\n"), 2)
        self.assertTrue(implementation.rstrip().endswith("(b))"))

    def test_usage_prefix_and_comment_are_not_definitions(self):
        self.assertIsNone(extract(self.SOURCE, "MAX", "C/C++"))
        self.assertIsNone(extract(self.SOURCE, "value", "C/C++"))
        self.assertIsNone(extract(self.SOURCE, "HIDDEN_VALUE", "C/C++"))
        self.assertIsNone(extract(self.SOURCE, "MAX_SIZE", "Python"))

    def test_block_comment_is_rejected(self):
        source = "/*\n#define HIDDEN 1\n*/\n"
        self.assertIsNone(extract(source, "HIDDEN", "C/C++"))

    def test_rust_macro_rules(self):
        source = (
            "macro_rules! my_macro {\n"
            "    ($x:expr) => {\n"
            '        println!("{}", $x);\n'
            "    };\n"
            "}\n"
        )
        signature, implementation, start, end = extract(
            source, "my_macro", "Rust"
        )
        self.assertEqual(signature, "macro_rules! my_macro")
        self.assertEqual((start, end), (1, 5))
        self.assertEqual(implementation.count("\n"), 4)

    def test_csharp_define(self):
        self.assertEqual(
            extract("#define FEATURE_FLAG\n", "FEATURE_FLAG", "C#"),
            ("#define FEATURE_FLAG", "#define FEATURE_FLAG", 1, 1),
        )


class SearchEngineMacroTests(unittest.TestCase):
    SOURCE = (
        "#define MAX_SIZE 100\n"
        "#define MIN(a, b) ((a) < (b) ? (a) : (b))\n"
        "#define SUM(a, b) \\\n"
        "    ((a) + (b))\n"
    )

    @staticmethod
    def _available_backends():
        backends = ["python"]
        if os.name != "nt" and shutil.which("grep"):
            backends.append("grep")
        if shutil.which("rg"):
            backends.append("rg")
        return backends

    def _search(self, backend, name, case_sensitive=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "defs.h").write_text(self.SOURCE, encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"FUNC_FINDER_BACKEND": backend}
            ):
                engine = func_finder.SearchEngine(threading.Event())
                return list(
                    engine.search(
                        str(root),
                        name,
                        func_finder.SEARCH_MODE_MACRO,
                        case_sensitive=case_sensitive,
                    )
                )

    def test_all_backends_find_macros_and_keep_actual_spelling(self):
        for backend in self._available_backends():
            with self.subTest(backend=backend):
                found = self._search(
                    backend, "max_size", case_sensitive=False
                )
                self.assertEqual([result.name for result in found], ["MAX_SIZE"])
                self.assertEqual(
                    found[0].kind, func_finder.SEARCH_MODE_MACRO
                )
                self.assertEqual(
                    found[0].implementation, "#define MAX_SIZE 100"
                )
                self.assertEqual(
                    (found[0].start_line, found[0].end_line), (1, 1)
                )

    def test_function_like_and_multiline(self):
        found = self._search("python", "SUM")
        self.assertEqual(len(found), 1)
        self.assertEqual(
            (found[0].start_line, found[0].end_line), (3, 4)
        )

    def test_macro_scope_filter(self):
        result = func_finder.SearchResult(
            name="MAX_SIZE",
            path="/src/a.h",
            start_line=1,
            end_line=1,
            signature="#define MAX_SIZE",
            implementation="#define MAX_SIZE 100",
            language="C/C++",
            kind=func_finder.SEARCH_MODE_MACRO,
        )
        self.assertTrue(func_finder.result_matches_filter(result, "", "macro"))
        self.assertFalse(
            func_finder.result_matches_filter(result, "", "function")
        )
        self.assertFalse(
            func_finder.result_matches_filter(result, "", "global")
        )
        self.assertTrue(
            func_finder.result_matches_filter(result, "max_size", "macro")
        )


if __name__ == "__main__":
    unittest.main()
