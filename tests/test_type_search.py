"""类型定义查询的纯逻辑测试（不需要显示器）。"""

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
        found = func_finder.extract_type(lines, index, name, language)
        if found:
            return found
    return None


class CTypeExtractionTests(unittest.TestCase):
    SOURCE = (
        "struct Point {\n"
        "    int x;\n"
        "};\n"
        "typedef struct {\n"
        "    int a;\n"
        "} Foo;\n"
        "typedef struct Bar {\n"
        "    int b;\n"
        "} Bar;\n"
        "typedef unsigned long uint32;\n"
        "typedef void (*Callback)(int);\n"
        "using Alias = Foo;\n"
        "struct Point *get_point(void) { return 0; }\n"
        "struct Point value;\n"
        "struct Point;\n"
    )

    def test_struct_definition(self):
        self.assertEqual(
            extract(self.SOURCE, "Point", "C/C++"),
            ("struct Point", "struct Point {\n    int x;\n};", 1, 3),
        )

    def test_anonymous_typedef(self):
        signature, implementation, start, end = extract(
            self.SOURCE, "Foo", "C/C++"
        )
        self.assertEqual(signature, "typedef struct Foo")
        self.assertIn("int a;", implementation)
        self.assertEqual((start, end), (4, 6))

    def test_tagged_typedef(self):
        signature, implementation, start, end = extract(
            self.SOURCE, "Bar", "C/C++"
        )
        self.assertEqual(signature, "typedef struct Bar Bar")
        self.assertIn("int b;", implementation)
        self.assertEqual((start, end), (7, 9))

    def test_simple_and_function_pointer_typedef(self):
        self.assertEqual(
            extract(self.SOURCE, "uint32", "C/C++"),
            (
                "typedef unsigned long uint32;",
                "typedef unsigned long uint32;",
                10,
                10,
            ),
        )
        self.assertEqual(
            extract(self.SOURCE, "Callback", "C/C++"),
            (
                "typedef void (*Callback)(int);",
                "typedef void (*Callback)(int);",
                11,
                11,
            ),
        )

    def test_using_alias(self):
        signature, implementation, start, end = extract(
            self.SOURCE, "Alias", "C/C++"
        )
        self.assertEqual(signature, "using Alias = Foo;")
        self.assertEqual(implementation, "using Alias = Foo;")
        self.assertEqual((start, end), (12, 12))

    def test_alias_on_next_line(self):
        source = (
            "typedef struct {\n"
            "    int x;\n"
            "}\n"
            "NextLine;\n"
        )
        signature, implementation, start, end = extract(
            source, "NextLine", "C/C++"
        )
        self.assertEqual(signature, "typedef struct NextLine")
        self.assertIn("int x;", implementation)
        self.assertEqual((start, end), (1, 4))

    def test_function_return_and_variable_uses_are_not_types(self):
        self.assertIsNone(extract(self.SOURCE, "get_point", "C/C++"))
        self.assertIsNone(extract(self.SOURCE, "value", "C/C++"))

    def test_forward_declaration_is_ignored(self):
        source = "struct Forward;\n"
        self.assertIsNone(extract(source, "Forward", "C/C++"))

    def test_objective_c_enum_macros(self):
        source = (
            "typedef NS_ENUM(NSInteger, FooStatus) {\n"
            "    FooStatusIdle,\n"
            "};\n"
            "typedef NS_OPTIONS(NSUInteger, FooFlags) {\n"
            "    FooFlagNone = 0,\n"
            "};\n"
        )
        self.assertEqual(
            extract(source, "FooStatus", "Objective-C"),
            (
                "typedef NS_ENUM(NSInteger, FooStatus)",
                "typedef NS_ENUM(NSInteger, FooStatus) {\n"
                "    FooStatusIdle,\n"
                "};",
                1,
                3,
            ),
        )
        signature, implementation, start, end = extract(
            source, "FooFlags", "Objective-C"
        )
        self.assertEqual(
            signature, "typedef NS_OPTIONS(NSUInteger, FooFlags)"
        )
        self.assertIn("FooFlagNone = 0,", implementation)
        self.assertEqual((start, end), (4, 6))


class PythonTypeExtractionTests(unittest.TestCase):
    SOURCE = (
        "@decorator\n"
        "class Config(Base):\n"
        "    x = 1\n"
        "\n"
        "    def load(self):\n"
        "        pass\n"
        "\n"
        "class OneLine: pass\n"
    )

    def test_class_with_decorator_and_body(self):
        signature, implementation, start, end = extract(
            self.SOURCE, "Config", "Python"
        )
        self.assertEqual(signature, "class Config(Base):")
        self.assertIn("@decorator", implementation)
        self.assertEqual((start, end), (1, 6))

    def test_one_line_class(self):
        signature, implementation, start, end = extract(
            self.SOURCE, "OneLine", "Python"
        )
        self.assertEqual(signature, "class OneLine:")
        self.assertEqual((start, end), (8, 8))


class OtherLanguageTypeExtractionTests(unittest.TestCase):
    def test_java_and_csharp(self):
        source = (
            "public class Config extends Base {\n"
            "}\n"
            "public interface Status { }\n"
            "public enum Color { RED }\n"
            "public record User(int id) { }\n"
        )
        self.assertEqual(
            extract(source, "Config", "Java")[0],
            "public class Config extends Base",
        )
        self.assertEqual(
            extract(source, "Status", "Java")[0],
            "public interface Status",
        )
        self.assertEqual(
            extract(source, "Color", "Java")[0],
            "public enum Color",
        )
        self.assertEqual(
            extract(source, "User", "Java")[0],
            "public record User(int id)",
        )

        csharp = (
            "public class Store { }\n"
            "public record User(int Id);\n"
            "public record struct Pair(int A, int B);\n"
        )
        self.assertEqual(
            extract(csharp, "Store", "C#")[0], "public class Store"
        )
        self.assertEqual(
            extract(csharp, "User", "C#")[0],
            "public record User(int Id);",
        )
        self.assertEqual(
            extract(csharp, "Pair", "C#")[0],
            "public record struct Pair(int A, int B);",
        )

    def test_go_including_group_and_alias(self):
        source = (
            "type Config struct {\n"
            "    Name string\n"
            "}\n"
            "type Status int\n"
            "type (\n"
            "    User struct {\n"
            "        ID int\n"
            "    }\n"
            "    Count int\n"
            ")\n"
        )
        self.assertEqual(
            extract(source, "Config", "Go")[0], "type Config struct"
        )
        self.assertEqual(
            extract(source, "Status", "Go")[0], "type Status int"
        )
        signature, implementation, start, end = extract(
            source, "User", "Go"
        )
        self.assertEqual(signature, "type User struct")
        self.assertIn("ID int", implementation)
        self.assertEqual((start, end), (6, 8))
        self.assertEqual(
            extract(source, "Count", "Go")[0], "type Count int"
        )

    def test_rust_struct_enum_trait_and_aliases(self):
        source = (
            "pub struct Config<T> {\n"
            "    value: T,\n"
            "}\n"
            "pub enum Status { Ok }\n"
            "pub trait Service { }\n"
            "pub type Alias = Config<u8>;\n"
            "pub struct Unit;\n"
            "pub struct Pair(u32, u32);\n"
        )
        self.assertEqual(
            extract(source, "Config", "Rust")[0],
            "pub struct Config<T>",
        )
        self.assertEqual(
            extract(source, "Status", "Rust")[0], "pub enum Status"
        )
        self.assertEqual(
            extract(source, "Service", "Rust")[0],
            "pub trait Service",
        )
        self.assertEqual(
            extract(source, "Alias", "Rust")[0],
            "pub type Alias = Config<u8>;",
        )
        self.assertEqual(
            extract(source, "Unit", "Rust")[0], "pub struct Unit;"
        )
        self.assertEqual(
            extract(source, "Pair", "Rust")[0],
            "pub struct Pair(u32, u32);",
        )

    def test_typescript_kotlin_swift_scala_dart(self):
        ts = (
            "export class Config { }\n"
            "interface Status { }\n"
            "type Alias = { a: string };\n"
            "enum Color { Red }\n"
        )
        self.assertEqual(
            extract(ts, "Config", "TypeScript")[0],
            "export class Config",
        )
        self.assertEqual(
            extract(ts, "Status", "TypeScript")[0], "interface Status"
        )
        self.assertEqual(
            extract(ts, "Alias", "TypeScript")[0],
            "type Alias = { a: string };",
        )
        self.assertEqual(
            extract(ts, "Color", "TypeScript")[0], "enum Color"
        )

        kotlin = (
            "data class Config(val x: Int) : Base() { }\n"
            "typealias Alias = Config\n"
            "enum class Color { RED }\n"
            "object Singleton { }\n"
        )
        self.assertEqual(
            extract(kotlin, "Config", "Kotlin")[0],
            "data class Config(val x: Int) : Base()",
        )
        self.assertEqual(
            extract(kotlin, "Alias", "Kotlin")[0],
            "typealias Alias = Config",
        )
        self.assertEqual(
            extract(kotlin, "Color", "Kotlin")[0], "enum class Color"
        )
        self.assertEqual(
            extract(kotlin, "Singleton", "Kotlin")[0],
            "object Singleton",
        )

        swift = (
            "struct Config: Codable { }\n"
            "protocol Service { }\n"
            "typealias Alias = Config\n"
            "actor Worker { }\n"
        )
        self.assertEqual(
            extract(swift, "Config", "Swift")[0],
            "struct Config: Codable",
        )
        self.assertEqual(
            extract(swift, "Service", "Swift")[0], "protocol Service"
        )
        self.assertEqual(
            extract(swift, "Alias", "Swift")[0],
            "typealias Alias = Config",
        )
        self.assertEqual(
            extract(swift, "Worker", "Swift")[0], "actor Worker"
        )

        scala = (
            "case class Config(x: Int) extends Base { }\n"
            "trait Service { }\n"
            "type Alias = Config\n"
            "object Singleton { }\n"
        )
        self.assertEqual(
            extract(scala, "Config", "Scala")[0],
            "case class Config(x: Int) extends Base",
        )
        self.assertEqual(
            extract(scala, "Service", "Scala")[0], "trait Service"
        )
        self.assertEqual(
            extract(scala, "Alias", "Scala")[0], "type Alias = Config"
        )
        self.assertEqual(
            extract(scala, "Singleton", "Scala")[0], "object Singleton"
        )

        dart = (
            "class Config extends Base { }\n"
            "enum Color { red }\n"
            "typedef Alias = Config;\n"
            "mixin M on Base { }\n"
        )
        self.assertEqual(
            extract(dart, "Config", "Dart")[0],
            "class Config extends Base",
        )
        self.assertEqual(
            extract(dart, "Color", "Dart")[0], "enum Color"
        )
        self.assertEqual(
            extract(dart, "Alias", "Dart")[0],
            "typedef Alias = Config;",
        )
        self.assertEqual(
            extract(dart, "M", "Dart")[0], "mixin M on Base"
        )


class SearchEngineTypeTests(unittest.TestCase):
    SOURCE = (
        "struct Point {\n"
        "    int x;\n"
        "};\n"
        "typedef struct {\n"
        "    int a;\n"
        "} Foo;\n"
        "typedef struct Bar {\n"
        "    int b;\n"
        "} Bar;\n"
        "struct Point *get_point(void) { return 0; }\n"
        "struct Point value;\n"
        "struct Point;\n"
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
                        func_finder.SEARCH_MODE_TYPE,
                        case_sensitive=case_sensitive,
                    )
                )

    def test_all_backends_find_types_without_duplicates(self):
        for backend in self._available_backends():
            with self.subTest(backend=backend):
                point = self._search(backend, "point", case_sensitive=False)
                self.assertEqual([item.name for item in point], ["Point"])
                self.assertEqual(
                    point[0].kind, func_finder.SEARCH_MODE_TYPE
                )
                self.assertEqual(
                    (point[0].start_line, point[0].end_line), (1, 3)
                )

                bar = self._search(backend, "Bar")
                self.assertEqual(len(bar), 1)
                self.assertEqual(
                    (bar[0].start_line, bar[0].end_line), (7, 9)
                )

    def test_non_type_uses_are_not_returned(self):
        self.assertEqual(self._search("python", "get_point"), [])
        self.assertEqual(self._search("python", "value"), [])

    def test_type_scope_filter(self):
        result = func_finder.SearchResult(
            name="Point",
            path="/src/a.h",
            start_line=1,
            end_line=3,
            signature="struct Point",
            implementation="struct Point {\n int x;\n};",
            language="C/C++",
            kind=func_finder.SEARCH_MODE_TYPE,
        )
        self.assertTrue(
            func_finder.result_matches_filter(result, "", "type_def")
        )
        self.assertFalse(
            func_finder.result_matches_filter(result, "", "function")
        )
        self.assertFalse(
            func_finder.result_matches_filter(result, "", "macro")
        )
        self.assertFalse(
            func_finder.result_matches_filter(result, "", "global")
        )
        self.assertTrue(
            func_finder.result_matches_filter(result, "point", "type_def")
        )


if __name__ == "__main__":
    unittest.main()
