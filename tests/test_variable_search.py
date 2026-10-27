# -*- coding: utf-8 -*-
"""变量定义查询的纯逻辑测试（不需要显示器）。"""

from __future__ import annotations

import os
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
        found = func_finder.extract_variable(lines, index, name, language)
        if found:
            return found
    return None


class PythonVariableTests(unittest.TestCase):
    SOURCE = '''"""Module docstring.
docstring_name = 1
"""

MAX_SIZE = 10
config: dict[str, int] = {
    "a": 1,
}


class Config:
    class_attr = 1
    annotated: str

    def load(self):
        local = 2
        self.instance_attr: int = 3
        obj.other = 4
        for name in (1, 2):
            pass
        return local


def function(name):
    return name
'''

    def test_module_and_annotated_constant(self):
        self.assertEqual(
            extract(self.SOURCE, "MAX_SIZE", "Python")[0], "MAX_SIZE = 10"
        )
        found = extract(self.SOURCE, "config", "Python")
        self.assertEqual(found[0], "config: dict[str, int] = {")
        self.assertEqual(found[4], "global")
        self.assertEqual(found[2], 6)
        self.assertEqual(found[3], 8)

    def test_class_instance_and_local_scopes(self):
        self.assertEqual(extract(self.SOURCE, "class_attr", "Python")[4], "class")
        self.assertEqual(extract(self.SOURCE, "annotated", "Python")[4], "class")
        self.assertEqual(extract(self.SOURCE, "local", "Python")[4], "local")
        self.assertEqual(
            extract(self.SOURCE, "instance_attr", "Python")[4], "instance"
        )

    def test_rejects_docstring_dict_key_assignment_and_parameter(self):
        self.assertIsNone(extract(self.SOURCE, "docstring_name", "Python"))
        self.assertIsNone(extract(self.SOURCE, "other", "Python"))
        self.assertIsNone(extract(self.SOURCE, "name", "Python"))

    def test_tuple_unpacking(self):
        source = "first, second = 1, 2\n"
        self.assertIn("first, second = 1, 2", extract(source, "second", "Python")[1])

    def test_last_line_without_newline(self):
        found = extract("MAX = 1  # trailing comment", "MAX", "Python")
        self.assertEqual(found[0], "MAX = 1")
        self.assertEqual(found[2:4], (1, 1))


class CVariableTests(unittest.TestCase):
    SOURCE = '''#define MAX_SIZE 10

static const int MAX_BUFFER_SIZE = 128;
int global_count;
int other, global_second;

struct Config {
    int count;
    const char *name;
};

static const int table[] = {
    1, 2, 3,
};

void load(void) {
    unsigned long timeout = 0;
    global_count = timeout;
}

int parse_config(int value);
'''

    def test_global_const_and_array_with_multiline_initializer(self):
        found = extract(self.SOURCE, "MAX_BUFFER_SIZE", "C/C++")
        self.assertEqual(found[0], "static const int MAX_BUFFER_SIZE = 128;")
        self.assertEqual(found[4], "global")

        found = extract(self.SOURCE, "table", "C/C++")
        self.assertIn("1, 2, 3", found[1])
        self.assertEqual((found[2], found[3]), (12, 14))

    def test_local_member_and_multiple_declarators(self):
        self.assertEqual(extract(self.SOURCE, "timeout", "C/C++")[4], "local")
        self.assertEqual(extract(self.SOURCE, "count", "C/C++")[4], "member")
        self.assertEqual(extract(self.SOURCE, "global_second", "C/C++")[4], "global")

    def test_rejects_macro_assignment_and_function(self):
        self.assertIsNone(extract(self.SOURCE, "MAX_SIZE", "C/C++"))
        # 同一名称既有定义又有赋值时，只返回定义。
        self.assertIsNotNone(extract(self.SOURCE, "global_count", "C/C++"))
        source = "void load(void) {\n    global_count = 3;\n}\n"
        self.assertIsNone(extract(source, "global_count", "C/C++"))
        self.assertIsNone(extract(self.SOURCE, "value", "C/C++"))

    def test_static_member_definition(self):
        found = extract(
            "static int Config::timeout = 0;\n", "timeout", "C/C++"
        )
        self.assertEqual(found[4], "member")
        self.assertEqual(found[0], "static int Config::timeout = 0;")


class DynamicLanguageVariableTests(unittest.TestCase):
    def test_javascript_and_typescript(self):
        source = '''const API_URL = "https://example.com";
let timeout = 30;
class Client {
  static version = 1;
  name: string;
  private cache = new Map();
  #secret = 2;
  method() { return this.name; }
}
interface Shape {
  title: string;
}
const { sourceKey: alias, other } = value;
const [first, second] = items;
'''
        self.assertEqual(extract(source, "API_URL", "JavaScript")[4], "global")
        self.assertEqual(
            extract(source, "API_URL", "JavaScript")[1],
            'const API_URL = "https://example.com";',
        )
        for name in ("version", "name", "cache", "secret"):
            self.assertEqual(
                extract(source, name, "TypeScript")[4], "member", name
            )
        self.assertEqual(extract(source, "alias", "TypeScript")[4], "global")
        self.assertEqual(extract(source, "other", "TypeScript")[4], "global")
        self.assertEqual(extract(source, "first", "TypeScript")[4], "global")
        self.assertIsNone(extract(source, "sourceKey", "TypeScript"))
        self.assertIsNone(extract(source, "title", "TypeScript"))
        self.assertIsNone(extract(source, "method", "TypeScript"))

        default_source = "const { fallback = defaultValue } = value;\n"
        self.assertIsNotNone(extract(default_source, "fallback", "TypeScript"))
        self.assertIsNone(
            extract(default_source, "defaultValue", "TypeScript")
        )

    def test_kotlin_swift_dart_and_scala(self):
        kotlin = '''private const val MAX_SIZE = 10

class Config(
    val name: String,
) {
    private val timeout: Int = 5
    fun load() {
        val local = 3
    }
}
'''
        self.assertEqual(extract(kotlin, "MAX_SIZE", "Kotlin")[4], "global")
        self.assertEqual(extract(kotlin, "name", "Kotlin")[4], "member")
        self.assertEqual(extract(kotlin, "timeout", "Kotlin")[4], "member")
        self.assertEqual(extract(kotlin, "local", "Kotlin")[4], "local")

        swift = '''let MAX_SIZE = 10
struct Config {
    var name: String = ""
    let timeout: Int = 5
}
func load() {
    let local = 3
}
'''
        self.assertEqual(extract(swift, "MAX_SIZE", "Swift")[4], "global")
        self.assertEqual(extract(swift, "name", "Swift")[4], "member")
        self.assertEqual(extract(swift, "local", "Swift")[4], "local")

        dart = '''const maxSize = 10;
class Config {
  final String name;
  var timeout = 5;
  static const version = 1;
}
void load() {
  final local = 3;
}
'''
        self.assertEqual(extract(dart, "maxSize", "Dart")[4], "global")
        self.assertEqual(extract(dart, "name", "Dart")[4], "member")
        self.assertEqual(extract(dart, "version", "Dart")[4], "member")
        self.assertEqual(extract(dart, "local", "Dart")[4], "local")

        scala = '''object Main {
  private val maxSize = 10
  var timeout: Int = 5
}
class Foo {
  lazy val name = "x"
  def load(): Unit = {
    val local = 3
  }
}
'''
        self.assertEqual(extract(scala, "maxSize", "Scala")[4], "member")
        self.assertEqual(extract(scala, "name", "Scala")[4], "member")
        self.assertEqual(extract(scala, "local", "Scala")[4], "local")


class TypedLanguageVariableTests(unittest.TestCase):
    def test_java_and_csharp(self):
        java = '''public class Config {
    public static final int MAX_SIZE = 10;
    private String name;
    private Map<String, Integer> cache = new HashMap<>();

    public void load() {
        int timeout = 5;
        name = "x";
    }
}
'''
        self.assertEqual(extract(java, "MAX_SIZE", "Java")[4], "member")
        self.assertEqual(extract(java, "name", "Java")[4], "member")
        self.assertEqual(extract(java, "cache", "Java")[4], "member")
        self.assertEqual(extract(java, "timeout", "Java")[4], "local")
        source = "public void load() {\n    name = \"x\";\n}\n"
        self.assertIsNone(extract(source, "name", "Java"))

        csharp = '''namespace Sample {
    public class Config {
        public const int MaxSize = 10;
        private string name;
        public int Property { get; set; }
        public int Expr => 3;

        public void Load() {
            var timeout = 5;
            int count = 0;
        }
    }
}
'''
        self.assertEqual(extract(csharp, "MaxSize", "C#")[4], "member")
        self.assertEqual(extract(csharp, "name", "C#")[4], "member")
        self.assertEqual(extract(csharp, "timeout", "C#")[4], "local")
        self.assertEqual(extract(csharp, "count", "C#")[4], "local")
        self.assertIsNone(extract(csharp, "Property", "C#"))
        self.assertIsNone(extract(csharp, "Expr", "C#"))


class GoAndRustVariableTests(unittest.TestCase):
    def test_go_var_group_short_and_struct(self):
        source = '''package main

const MaxSize = 10

var (
    timeout time.Duration = 5
    retries int
)

type Config struct {
    Name string
    Timeout time.Duration `json:"timeout"`
}

func main() {
    count := 3
    var local string = "x"
    retries = 4
}

func load(name string) {
}
'''
        self.assertEqual(extract(source, "MaxSize", "Go")[4], "global")
        self.assertEqual(extract(source, "timeout", "Go")[4], "global")
        self.assertEqual(extract(source, "retries", "Go")[4], "global")
        self.assertEqual(extract(source, "Name", "Go")[4], "member")
        self.assertEqual(extract(source, "Timeout", "Go")[4], "member")
        self.assertEqual(extract(source, "count", "Go")[4], "local")
        self.assertEqual(extract(source, "local", "Go")[4], "local")
        self.assertIsNone(extract(source, "name", "Go"))

    def test_rust_let_static_struct_and_impl(self):
        source = '''static MAX_SIZE: usize = 10;
static LABEL: &'static str = "config";

struct Config {
    pub name: String,
    timeout: u64,
}

impl Config {
    const DEFAULT_TIMEOUT: u64 = 5;
}

fn main() {
    let mut count = 3;
    let (a, local) = (1, 2);
    let Some(opt) = None::<u8>;
    count += 1;
}

fn load(name: String) {
}
'''
        self.assertEqual(extract(source, "MAX_SIZE", "Rust")[4], "global")
        label = extract(source, "LABEL", "Rust")
        self.assertEqual(label[4], "global")
        self.assertEqual(label[2], label[3])
        self.assertIn("&'static str", label[1])
        self.assertEqual(extract(source, "name", "Rust")[4], "member")
        self.assertEqual(extract(source, "timeout", "Rust")[4], "member")
        self.assertEqual(
            extract(source, "DEFAULT_TIMEOUT", "Rust")[4], "member"
        )
        self.assertEqual(extract(source, "count", "Rust")[4], "local")
        self.assertEqual(extract(source, "local", "Rust")[4], "local")
        self.assertEqual(extract(source, "opt", "Rust")[4], "local")
        self.assertEqual(extract(source, "a", "Rust")[4], "local")


class SearchEngineVariableTests(unittest.TestCase):
    def test_variable_and_function_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text(
                "MAX_SIZE = 10\n\n"
                "def parse_config(name):\n"
                "    local = name\n"
                "    return local\n",
                encoding="utf-8",
            )
            (root / "b.c").write_text(
                "static const int MAX_SIZE = 20;\n"
                "int global_count;\n"
                "void parse_config(int value) {\n"
                "    int local = value;\n"
                "}\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"FUNC_FINDER_BACKEND": "python"}
            ):
                variable_engine = func_finder.SearchEngine(
                    threading.Event()
                )
                variables = list(
                    variable_engine.search(
                        str(root),
                        "MAX_SIZE",
                        func_finder.SEARCH_MODE_VARIABLE,
                    )
                )
                self.assertEqual(len(variables), 2)
                self.assertTrue(
                    all(result.start_line == 1 for result in variables)
                )
                self.assertTrue(
                    all(
                        result.kind == func_finder.SEARCH_MODE_VARIABLE
                        for result in variables
                    )
                )
                self.assertTrue(all(result.scope for result in variables))

                function_engine = func_finder.SearchEngine(
                    threading.Event()
                )
                functions = list(
                    function_engine.search(str(root), "parse_config")
                )
                self.assertEqual(len(functions), 2)
                self.assertTrue(
                    all(
                        result.kind == func_finder.SEARCH_MODE_FUNCTION
                        for result in functions
                    )
                )

                local_engine = func_finder.SearchEngine(threading.Event())
                locals_found = list(
                    local_engine.search(
                        str(root),
                        "local",
                        func_finder.SEARCH_MODE_VARIABLE,
                    )
                )
                self.assertEqual(len(locals_found), 2)
                self.assertTrue(
                    all(result.scope == "local" for result in locals_found)
                )

    def test_default_mode_is_function(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.py").write_text(
                "value = 1\n\n"
                "def value():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"FUNC_FINDER_BACKEND": "python"}
            ):
                engine = func_finder.SearchEngine(threading.Event())
                results = list(engine.search(str(root), "value"))
                self.assertTrue(results)
                self.assertTrue(
                    all(
                        result.kind == func_finder.SEARCH_MODE_FUNCTION
                        for result in results
                    )
                )


if __name__ == "__main__":
    unittest.main()
