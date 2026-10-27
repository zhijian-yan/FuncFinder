#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用函数与变量定义查询工具（PySide6 / Qt 6）

功能：
    1. 配置任意代码目录（自动保存，跨平台）；
    2. 在「函数」模式下输入函数名，搜索其定义与完整实现；
       在「变量」模式下输入变量名，搜索变量/常量的声明与初始值；
    3. 结果以卡片形式展示签名、来源、行号及完整代码，代码带语法高亮；
    4. 支持单条/全部复制结果，以及浅色/深色主题切换；
    5. 搜索在后台线程执行，可随时停止；结果列表按需渲染，
       即使命中大量结果，切换主题/语言或继续输入也不会卡顿；
    6. 使用 assets/ 下的 FF 图标作为窗口和桌面图标；Linux 下首次运行
       会自动注册桌面入口（Ubuntu Dock 图标），也可用 --install-desktop
       手动注册。

支持语言（按扩展名自动识别）：
    C/C++、Python、Go、Rust、Java、C#、JavaScript/TypeScript、
    Kotlin、Scala、Swift、Dart、Objective-C（C 函数部分）。

依赖：
    pip install PySide6-Essentials
    可选：ripgrep（rg），默认优先使用；没有 rg 时自动使用系统 grep，
    最后回退到纯 Python 扫描。安装 ripgrep 可显著加快搜索。
    可通过 FUNC_FINDER_BACKEND=rg|grep|python 强制指定搜索后端。

用法：
    python3 func_finder.py
    python3 func_finder.py --version
    python3 func_finder.py --install-desktop
    python3 func_finder.py --uninstall-desktop
"""

import ast
import bisect
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    from PySide6.QtCore import (
        QPointF,
        QRectF,
        QRegularExpression,
        QSettings,
        QSize,
        Qt,
        QThread,
        QTimer,
        Signal,
    )
    from PySide6.QtGui import (
        QColor,
        QFont,
        QFontDatabase,
        QFontMetrics,
        QIcon,
        QPainter,
        QPen,
        QPixmap,
        QSyntaxHighlighter,
        QTextCharFormat,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QFrame,
        QGraphicsDropShadowEffect,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - 仅在缺少依赖时触发
    sys.exit(
        "无法导入 PySide6，请先安装依赖：\n"
        "    pip install PySide6-Essentials\n"
        f"原始错误：{exc}"
    )

APP_NAME = "FuncFinder"
__version__ = "0.2.0"
SEARCH_MODE_FUNCTION = "function"
SEARCH_MODE_VARIABLE = "variable"
SEARCH_MODES = (SEARCH_MODE_FUNCTION, SEARCH_MODE_VARIABLE)
# GNOME Wayland 用 setDesktopFileName() 的值匹配 .desktop 文件名，因此这里
# 保持 "func-finder"（不带 .desktop 后缀）。
DESKTOP_FILE_NAME = "func-finder"
DESKTOP_ENTRY_NAME = f"{DESKTOP_FILE_NAME}.desktop"
DESKTOP_ICON_NAME = DESKTOP_FILE_NAME
# Qt 在 X11 下把 applicationName() 写进 WM_CLASS 的 class 部分，程序里固定
# 是 APP_NAME；StartupWMClass 必须与它一致。
DESKTOP_STARTUP_WM_CLASS = APP_NAME

DESKTOP_COMMENT = "Search function and variable definitions"
DESKTOP_COMMENT_ZH = "搜索代码中的函数与变量定义"
DESKTOP_GENERIC_NAME = "Function & Variable Finder"
DESKTOP_GENERIC_NAME_ZH = "函数与变量查找器"
DESKTOP_CATEGORIES = "Development;"
DESKTOP_KEYWORDS = "function;variable;constant;search;code;symbol;grep;ripgrep;"
DESKTOP_KEYWORDS_ZH = "函数;变量;常量;搜索;代码;符号;"


def _resource_path(*parts):
    """定位源码运行或 PyInstaller 打包后的资源文件。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def _load_app_icon():
    # X11 的 _NET_WM_ICON 属性有大小上限，512px 图标可能写不进去；
    # 因此窗口图标优先使用 192px 版本，桌面启动器仍可使用 512px 版本。
    for name in ("func_finder_window.png", "func_finder.png"):
        icon_path = _resource_path("assets", name)
        if icon_path.is_file():
            return QIcon(str(icon_path))
    return QIcon()


# ---------------------------------------------------------------------------
# Linux 桌面集成：安装 .desktop 入口和 hicolor 图标
#
# PyInstaller 的 --icon 在 Linux 上不会把图标写进 ELF；Ubuntu Dock / GNOME
# 通过窗口的 Wayland app_id 或 X11 WM_CLASS 找到同名 .desktop 文件，再按
# 该文件的 Icon= 从图标主题读取图标。以下代码因此直接写在主程序里，
# 不再依赖单独的 desktop_integration.py。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesktopPaths:
    """桌面入口和图标在用户目录中的安装位置。"""

    data_home: Path
    desktop_file: Path
    icon_512: Path
    icon_192: Path


def _desktop_data_home() -> Path:
    """返回 XDG_DATA_HOME；未设置或不是绝对路径时退回 ~/.local/share。"""
    value = os.environ.get("XDG_DATA_HOME", "").strip()
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate
    return Path.home() / ".local" / "share"


def _desktop_installation_paths(data_home: Path | None = None) -> DesktopPaths:
    data = Path(data_home).expanduser() if data_home is not None else _desktop_data_home()
    icon_root = data / "icons" / "hicolor"
    return DesktopPaths(
        data_home=data,
        desktop_file=data / "applications" / DESKTOP_ENTRY_NAME,
        icon_512=icon_root / "512x512" / "apps" / f"{DESKTOP_ICON_NAME}.png",
        icon_192=icon_root / "192x192" / "apps" / f"{DESKTOP_ICON_NAME}.png",
    )


def _desktop_quote(argument: str) -> str:
    """按 Desktop Entry Specification 的规则给 Exec 参数加引号。"""
    if not argument:
        return '""'
    if any(character in argument for character in "\0\n\r"):
        raise ValueError(f"Exec 参数不能包含换行或 NUL：{argument!r}")

    escaped = argument.replace("\\", "\\\\")
    escaped = escaped.replace('"', '\\"')
    escaped = escaped.replace("`", "\\`")
    escaped = escaped.replace("$", "\\$")
    # 百分号是 .desktop 的字段码前缀，写成 %% 才是字面量。
    escaped = escaped.replace("%", "%%")
    return f'"{escaped}"'


def _desktop_exec_line(command: Sequence[str]) -> str:
    if not command:
        raise ValueError("Exec 命令不能为空")
    return " ".join(_desktop_quote(part) for part in command)


def _desktop_entry_text(
    command: Sequence[str], wm_class: str | None = None
) -> str:
    """生成 .desktop 文件内容。"""
    command = [str(part) for part in command]
    if not command:
        raise ValueError("Exec 命令不能为空")

    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        f"Name={APP_NAME}",
        f"GenericName={DESKTOP_GENERIC_NAME}",
        f"GenericName[zh_CN]={DESKTOP_GENERIC_NAME_ZH}",
        f"Comment={DESKTOP_COMMENT}",
        f"Comment[zh_CN]={DESKTOP_COMMENT_ZH}",
        f"Exec={_desktop_exec_line(command)}",
        f"Icon={DESKTOP_ICON_NAME}",
        "Terminal=false",
        f"Categories={DESKTOP_CATEGORIES}",
        f"Keywords={DESKTOP_KEYWORDS}",
        f"Keywords[zh_CN]={DESKTOP_KEYWORDS_ZH}",
        "StartupNotify=true",
        f"StartupWMClass={wm_class or DESKTOP_STARTUP_WM_CLASS}",
        "",
    ]
    return "\n".join(lines)


def _desktop_temp_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def _desktop_write_if_different(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeError):
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _desktop_temp_path(path)
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _desktop_copy_if_different(source: Path, destination: Path) -> bool:
    try:
        if source.resolve() == destination.resolve():
            return False
    except OSError:
        pass

    try:
        if (
            destination.is_file()
            and destination.stat().st_size == source.stat().st_size
            and destination.read_bytes() == source.read_bytes()
        ):
            return False
    except OSError:
        pass

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _desktop_temp_path(destination)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _desktop_install_icons(icon_path: Path, paths: DesktopPaths) -> bool:
    if not icon_path.is_file():
        raise FileNotFoundError(f"找不到图标文件：{icon_path}")

    changed = _desktop_copy_if_different(icon_path, paths.icon_512)

    # 192px 版本是 Qt 写 X11 _NET_WM_ICON 时使用的尺寸；有就一起安装。
    window_icon = icon_path.with_name("func_finder_window.png")
    if window_icon.is_file():
        changed = _desktop_copy_if_different(window_icon, paths.icon_192) or changed
    return changed


def _desktop_run_quietly(command: Sequence[str]) -> None:
    try:
        subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _desktop_refresh_database(data_home: Path | None = None) -> None:
    """通知桌面环境 .desktop 文件有变化；工具不存在时静默跳过。"""
    paths = _desktop_installation_paths(data_home)
    tool = shutil.which("update-desktop-database")
    if tool:
        _desktop_run_quietly([tool, str(paths.desktop_file.parent)])

    # 用户级 hicolor 目录通常没有 index.theme，此时不需要/也无法更新缓存。
    icon_root = paths.data_home / "icons" / "hicolor"
    cache_tool = shutil.which("gtk-update-icon-cache")
    if cache_tool and (icon_root / "index.theme").is_file():
        _desktop_run_quietly([cache_tool, "-f", "-t", str(icon_root)])


def _desktop_install(
    command: Sequence[str],
    icon_path: Path | str,
    data_home: Path | str | None = None,
) -> bool:
    """安装/更新桌面入口与图标，返回是否发生了修改。"""
    command = [str(part) for part in command]
    if not command:
        raise ValueError("桌面入口缺少启动命令")

    icon = Path(icon_path).expanduser()
    paths = _desktop_installation_paths(
        Path(data_home) if data_home is not None else None
    )

    # 先装图标再写 .desktop，避免桌面环境先看到入口却找不到图标。
    changed = _desktop_install_icons(icon, paths)
    changed = _desktop_write_if_different(
        paths.desktop_file, _desktop_entry_text(command)
    ) or changed

    if changed:
        _desktop_refresh_database(paths.data_home)
    return changed


def _desktop_uninstall(data_home: Path | str | None = None) -> list[Path]:
    """删除本程序安装的 .desktop 和图标，返回实际删除的文件。"""
    paths = _desktop_installation_paths(
        Path(data_home) if data_home is not None else None
    )
    removed = []
    for path in (paths.desktop_file, paths.icon_512, paths.icon_192):
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
    if removed:
        _desktop_refresh_database(paths.data_home)
    return removed


def _launch_command():
    """返回重新启动当前程序所需的命令（用于 .desktop 的 Exec）。"""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable))]
    return [str(Path(sys.executable)), str(Path(__file__).resolve())]


def _ensure_desktop_integration():
    """首次运行时自动注册桌面入口，让 Ubuntu Dock 能显示图标。"""
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("FUNC_FINDER_NO_DESKTOP_ENTRY"):
        return
    try:
        _desktop_install(
            _launch_command(), _resource_path("assets", "func_finder.png")
        )
    except Exception:
        # 桌面集成失败不应阻止程序启动。
        pass


def _handle_desktop_cli():
    """处理 --install-desktop / --uninstall-desktop。

    返回 None 表示当前不是桌面集成命令，否则返回进程退出码。
    """
    actions = ("--install-desktop", "--uninstall-desktop")
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        return None

    action = sys.argv[1]
    if not sys.platform.startswith("linux"):
        print("桌面集成仅支持 Linux。", file=sys.stderr)
        return 1

    try:
        if action == "--install-desktop":
            changed = _desktop_install(
                _launch_command(), _resource_path("assets", "func_finder.png")
            )
            print(
                "已注册 FuncFinder 桌面入口和 Dock 图标。"
                if changed
                else "FuncFinder 桌面入口已是最新。"
            )
            print("若 Dock 仍显示默认图标，请注销并重新登录。")
        else:
            removed = _desktop_uninstall()
            if removed:
                print(f"已删除 {len(removed)} 个桌面集成文件。")
            else:
                print("没有找到已安装的 FuncFinder 桌面入口。")
    except Exception as exc:
        print(f"桌面集成失败：{exc}", file=sys.stderr)
        return 1
    return 0


LANGUAGE_BY_SUFFIX = {
    ".c": "C/C++",
    ".h": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".cxx": "C/C++",
    ".hpp": "C/C++",
    ".hh": "C/C++",
    ".hxx": "C/C++",
    ".inl": "C/C++",
    ".ino": "C/C++",
    ".m": "Objective-C",
    ".mm": "Objective-C",
    ".java": "Java",
    ".cs": "C#",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".swift": "Swift",
    ".dart": "Dart",
    ".go": "Go",
    ".rs": "Rust",
    ".py": "Python",
    ".pyi": "Python",
}

# 这些语言的函数定义必须带关键字，避免把普通调用误判为定义
REQUIRED_KEYWORD = {
    "Go": r"\bfunc\b",
    "Rust": r"\bfn\b",
    "Kotlin": r"\bfun\b",
    "Swift": r"\bfunc\b",
    "Scala": r"\bdef\b",
}

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".repo",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    "target",
}

EXCLUDE_DIRS = tuple(sorted(SKIP_DIRS))

MAX_CONTEXT_LINES = 400
MAX_BODY_SCAN = 4000

_CONTROL_PREFIX_RE = re.compile(
    r"\b(?:if|while|for|switch|catch|return|sizeof|typeof|alignof|decltype)\s*\($"
)
_NEW_PREFIX_RE = re.compile(r"\bnew\s+$")

KEYWORDS = sorted(
    {
        word
        for word in """
        if else elif for while do switch case default break continue return goto
        try except finally raise with as pass yield lambda assert del global nonlocal
        class struct union enum typedef namespace template typename concept requires
        public private protected internal sealed abstract static const constexpr volatile
        inline virtual override final explicit friend mutable register extern auto
        void bool char short int long float double signed unsigned size_t ssize_t
        def async await import from in is not and or None True False
        fn let mut impl trait pub use mod match where loop crate self super Self dyn ref move
        unsafe
        func go defer chan map range package interface select fallthrough type
        fun val var when object companion init constructor lateinit
        function typeof instanceof this new delete sizeof alignof decltype nullptr NULL true false
        throws extends implements package import
        readonly record
        guard defer protocol extension mutating
        """.split()
    }
)
_KEYWORD_RE = QRegularExpression(r"\b(?:" + "|".join(KEYWORDS) + r")\b")
_NUMBER_RE = QRegularExpression(r"\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)\b")
_STRING_COMMENT_RE = QRegularExpression(
    r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|#[^\n]*)'
)

TRANSLATIONS = {
    "zh": {
        "app_title": "函数与变量定义查询",
        "title": "函数与变量定义查询",
        "subtitle": "在任意代码目录中搜索函数、变量与常量的定义",
        "code_dir": "代码目录",
        "path_placeholder": "选择任意代码目录，例如 /home/user/project",
        "browse": "选择目录",
        "apply": "应用",
        "choose_dir_title": "选择代码目录",
        "path_configured": "已配置：{path}",
        "path_saved": "已保存：{path}",
        "path_not_configured": "尚未配置代码目录，请选择目录后点击“应用”。",
        "path_empty_error": "请先选择代码目录。",
        "path_missing_error": "目录不存在：{path}",
        "mode_function": "函数",
        "mode_variable": "变量",
        "mode_function_tooltip": "搜索函数定义与完整实现",
        "mode_variable_tooltip": "搜索变量、常量的定义与初始值",
        "function_name": "函数名称",
        "variable_name": "变量名称",
        "search_placeholder": "例如：i2c_transfer、parse_config、main",
        "variable_placeholder": "例如：MAX_BUFFER_SIZE、config、timeout",
        "search": "搜索",
        "stop": "停止",
        "hint": (
            "支持 C/C++、Python、Go、Rust、Java、C#、JavaScript/TypeScript、"
            "Kotlin、Scala、Swift、Dart 等，按文件扩展名自动识别"
        ),
        "variable_hint": (
            "支持全局/局部变量、常量以及结构体/类字段，覆盖与函数模式相同的语言"
        ),
        "hint_install_rg": (
            "当前使用 {engine} 引擎；安装 ripgrep（rg）可显著加快搜索。"
        ),
        "status_initial": "请先配置代码目录",
        "status_ready": "代码目录已就绪，输入名称开始搜索",
        "status_not_configured": "尚未配置代码目录",
        "status_invalid_dir": "代码目录无效",
        "results_title": "搜索结果",
        "results_count": "{count} 个结果",
        "copy_all": "复制全部",
        "copy_all_tooltip": "复制当前所有搜索结果的代码",
        "empty_initial": (
            "配置代码目录，输入函数名开始搜索\n"
            "支持 C/C++、Python、Go、Rust、Java、C#、JavaScript/TypeScript、"
            "Kotlin、Swift 等"
        ),
        "empty_initial_variable": (
            "配置代码目录，输入变量名开始搜索\n"
            "支持全局/局部变量、常量，以及结构体/类字段"
        ),
        "empty_no_results": (
            "未找到函数定义或实现\n"
            "可尝试其他函数名，或确认代码目录和文件扩展名是否受支持"
        ),
        "empty_no_results_variable": (
            "未找到变量定义\n"
            "可尝试其他变量名，或确认代码目录和文件扩展名是否受支持"
        ),
        "empty_failed": "搜索出错，请检查代码目录是否可读",
        "theme_dark": "深色模式",
        "theme_light": "浅色模式",
        "theme_tooltip": "切换界面主题",
        "lang_tooltip": "切换语言",
        "error_empty_name": "请输入要查询的函数名。",
        "error_empty_variable_name": "请输入要查询的变量名。",
        "error_invalid_name": (
            "函数名不合法：只能包含字母、数字、下划线、$，且不能以数字开头。"
        ),
        "error_invalid_variable_name": (
            "变量名不合法：只能包含字母、数字、下划线、$，且不能以数字开头。"
        ),
        "error_no_dir": "请先配置有效的代码目录。",
        "searching": "正在搜索 “{name}” ...（引擎：{engine}）",
        "stopping": "正在停止搜索 “{name}” ...",
        "kind_function": "函数",
        "kind_variable": "变量",
        "searching_progress": (
            "正在搜索 “{name}” ... 已扫描 {scanned} 个候选，找到 {count} 个{kind}"
        ),
        "searching_found": "正在搜索 “{name}” ... 已找到 {count} 个{kind}",
        "finished_summary": (
            "搜索完成：扫描 {scanned} 个候选，找到 {count} 个{kind}，耗时 {seconds:.2f} 秒"
        ),
        "stopped_summary": (
            "搜索已停止：扫描 {scanned} 个候选，找到 {count} 个{kind}，耗时 {seconds:.2f} 秒"
        ),
        "search_failed": "搜索出错：{message}",
        "copied_all": "已复制 {count} 个结果的代码到剪贴板",
        "lines_range": "第 {start} - {end} 行",
        "implementation": "完整实现",
        "variable_definition": "变量定义",
        "scope_global": "全局变量",
        "scope_local": "局部变量",
        "scope_member": "成员变量",
        "scope_class": "类属性",
        "scope_instance": "实例属性",
        "scope_type": "类型成员",
        "copy_tooltip": "复制该函数的完整实现代码",
        "copy_signature_tooltip": "复制函数声明",
        "copy_variable_tooltip": "复制该变量的定义代码",
        "copy_variable_signature_tooltip": "复制变量声明",
        "copied": "已复制",
    },
    "en": {
        "app_title": "Function & Variable Finder",
        "title": "Function & Variable Finder",
        "subtitle": "Search function, variable, and constant definitions in any code directory",
        "code_dir": "Code directory",
        "path_placeholder": "Choose any code directory, e.g. /home/user/project",
        "browse": "Browse...",
        "apply": "Apply",
        "choose_dir_title": "Choose code directory",
        "path_configured": "Configured: {path}",
        "path_saved": "Saved: {path}",
        "path_not_configured": "No code directory configured. Choose one and click Apply.",
        "path_empty_error": "Please choose a code directory first.",
        "path_missing_error": "Directory does not exist: {path}",
        "mode_function": "Function",
        "mode_variable": "Variable",
        "mode_function_tooltip": "Search function definitions and implementations",
        "mode_variable_tooltip": "Search variable and constant definitions",
        "function_name": "Function name",
        "variable_name": "Variable name",
        "search_placeholder": "e.g. i2c_transfer, parse_config, main",
        "variable_placeholder": "e.g. MAX_BUFFER_SIZE, config, timeout",
        "search": "Search",
        "stop": "Stop",
        "hint": (
            "Supports C/C++, Python, Go, Rust, Java, C#, JavaScript/TypeScript, "
            "Kotlin, Scala, Swift, Dart, etc. Detected by file extension."
        ),
        "variable_hint": (
            "Supports global/local variables, constants, and struct/class fields "
            "across the same languages as function search."
        ),
        "hint_install_rg": (
            "Searching with the {engine} engine. "
            "Install ripgrep (rg) for much faster searches."
        ),
        "status_initial": "Configure a code directory first",
        "status_ready": "Code directory ready. Enter a name to search.",
        "status_not_configured": "No code directory configured",
        "status_invalid_dir": "Invalid code directory",
        "results_title": "Results",
        "results_count": "{count} results",
        "copy_all": "Copy all",
        "copy_all_tooltip": "Copy the code of all current results",
        "empty_initial": (
            "Configure a code directory and enter a function name to start\n"
            "Supports C/C++, Python, Go, Rust, Java, C#, JavaScript/TypeScript, "
            "Kotlin, Swift, etc."
        ),
        "empty_initial_variable": (
            "Configure a code directory and enter a variable name to start\n"
            "Supports global/local variables, constants, and struct/class fields"
        ),
        "empty_no_results": (
            "No function definition or implementation found\n"
            "Try another name, or check the code directory and supported file extensions"
        ),
        "empty_no_results_variable": (
            "No variable definition found\n"
            "Try another name, or check the code directory and supported file extensions"
        ),
        "empty_failed": "Search failed. Check that the code directory is readable.",
        "theme_dark": "Dark mode",
        "theme_light": "Light mode",
        "theme_tooltip": "Switch UI theme",
        "lang_tooltip": "Switch language",
        "error_empty_name": "Please enter a function name.",
        "error_empty_variable_name": "Please enter a variable name.",
        "error_invalid_name": (
            "Invalid function name: use letters, digits, underscores, or $, "
            "and do not start with a digit."
        ),
        "error_invalid_variable_name": (
            "Invalid variable name: use letters, digits, underscores, or $, "
            "and do not start with a digit."
        ),
        "error_no_dir": "Configure a valid code directory first.",
        "searching": "Searching for “{name}” ... (engine: {engine})",
        "stopping": "Stopping “{name}” ...",
        "kind_function": "function",
        "kind_variable": "variable",
        "searching_progress": (
            "Searching “{name}” ... scanned {scanned} candidates, "
            "definitions found: {count}"
        ),
        "searching_found": "Searching “{name}” ... definitions found: {count}",
        "finished_summary": (
            "Search complete: scanned {scanned} candidates, "
            "definitions found: {count} in {seconds:.2f}s"
        ),
        "stopped_summary": (
            "Search stopped: scanned {scanned} candidates, "
            "definitions found: {count} in {seconds:.2f}s"
        ),
        "search_failed": "Search failed: {message}",
        "copied_all": "Copied code from {count} results to clipboard",
        "lines_range": "Lines {start} - {end}",
        "implementation": "Implementation",
        "variable_definition": "Variable definition",
        "scope_global": "Global variable",
        "scope_local": "Local variable",
        "scope_member": "Member",
        "scope_class": "Class attribute",
        "scope_instance": "Instance attribute",
        "scope_type": "Type member",
        "copy_tooltip": "Copy the function's implementation code",
        "copy_signature_tooltip": "Copy function declaration",
        "copy_variable_tooltip": "Copy the variable's definition code",
        "copy_variable_signature_tooltip": "Copy variable declaration",
        "copied": "Copied",
    },
}


def tr(lang, key, **kwargs):
    table = TRANSLATIONS.get(lang) or TRANSLATIONS["zh"]
    text = table.get(key, key)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, ValueError):
        return text


def results_count_text(lang, count):
    if lang == "en":
        return f"{count} result" if count == 1 else f"{count} results"
    return tr(lang, "results_count", count=count)


@dataclass
class SearchResult:
    name: str
    path: str
    start_line: int
    end_line: int
    signature: str
    implementation: str
    language: str
    # "function" 或 "variable"；默认值保证旧调用方式仍然可用。
    kind: str = SEARCH_MODE_FUNCTION
    # 变量所在作用域：global / local / member / class / instance / type。
    scope: str = ""


# ---------------------------------------------------------------------------
# C 风格源码解析
# ---------------------------------------------------------------------------


def _strip_comments(text):
    """移除完整的块注释和行注释；存在未闭合块注释时返回 None。"""
    result = []
    idx = 0
    length = len(text)
    while idx < length:
        if text.startswith("/*", idx):
            end = text.find("*/", idx + 2)
            if end < 0:
                return None
            idx = end + 2
            continue
        if text.startswith("//", idx):
            break
        result.append(text[idx])
        idx += 1
    return "".join(result)


def _prefix_is_code(prefix):
    """判断函数名之前的同一行内容是否是代码（而非注释、宏定义或赋值）。"""
    if prefix.lstrip().startswith("#"):
        return False
    stripped = _strip_comments(prefix)
    if stripped is None:
        return False
    if "=" in stripped:
        return False
    if stripped.rstrip().endswith((".", "->")):
        return False
    return True


def _in_block_comment(lines, line_idx):
    """粗略判断 line_idx 是否位于跨行块注释内。"""
    lower = max(-1, line_idx - 300)
    for idx in range(line_idx - 1, lower, -1):
        line = lines[idx]
        open_pos = line.rfind("/*")
        close_pos = line.rfind("*/")
        if open_pos < 0 and close_pos < 0:
            continue
        return open_pos > close_pos
    return False


def _signature_start(lines, line_idx):
    """向上收集可能位于前几行的返回类型、注解、模板等。"""
    start = line_idx
    prev_idx = line_idx - 1
    while prev_idx >= 0 and line_idx - prev_idx <= 3:
        prev = lines[prev_idx].strip()
        if (
            not prev
            or prev.startswith(("#", "/*", "*", "//"))
            or prev.endswith((";", "{", "}", "*/", ":"))
        ):
            break
        start = prev_idx
        prev_idx -= 1
    return start


def _matching_pair(text, open_idx, opener, closer):
    """返回与 open_idx 处 opener 匹配的 closer 下标，忽略注释和字符串。"""
    depth = 0
    idx = open_idx
    state = None  # None / "block" / "line" / "str" / "chr"
    length = len(text)
    while idx < length:
        char = text[idx]
        nxt = text[idx + 1] if idx + 1 < length else ""
        if state == "block":
            if char == "*" and nxt == "/":
                state = None
                idx += 1
        elif state == "line":
            if char == "\n":
                state = None
        elif state == "str":
            if char == "\\":
                idx += 1
            elif char == '"':
                state = None
        elif state == "chr":
            if char == "\\":
                idx += 1
            elif char == "'":
                state = None
        else:
            if char == "/" and nxt == "*":
                state = "block"
                idx += 1
            elif char == "/" and nxt == "/":
                state = "line"
                idx += 1
            elif char == '"':
                state = "str"
            elif char == "'":
                state = "chr"
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return idx
        idx += 1
    return -1


def _matching_paren(text, open_idx):
    return _matching_pair(text, open_idx, "(", ")")


def _matching_brace(text, open_idx):
    return _matching_pair(text, open_idx, "{", "}")


def _body_open(text, idx, allow_group_close=False):
    """从参数列表之后寻找函数体 '{'，返回其下标；不是定义则返回 -1。"""
    length = min(len(text), idx + MAX_BODY_SCAN)
    while idx < length:
        char = text[idx]
        if char in " \t\r\n\\":
            idx += 1
            continue
        if char == "/" and idx + 1 < length and text[idx + 1] == "*":
            end = text.find("*/", idx + 2)
            if end < 0:
                return -1
            idx = end + 2
            continue
        if char == "/" and idx + 1 < length and text[idx + 1] == "/":
            end = text.find("\n", idx + 2)
            if end < 0:
                return -1
            idx = end + 1
            continue
        if char == "{":
            return idx
        if char in ";=":
            return -1
        if char == ")":
            if allow_group_close:
                idx += 1
                continue
            return -1
        if char == "(":
            close = _matching_paren(text, idx)
            if close < 0:
                return -1
            idx = close + 1
            continue
        if char.isalnum() or char == "_" or char in ":*&,[]?!.-<>":
            idx += 1
            continue
        return -1
    return -1


def _skip_bracket_group(line, idx, opener, closer):
    """跳过空白后可选的一个 <...> 或 [...] 分组。"""
    while idx < len(line) and line[idx].isspace():
        idx += 1
    if idx >= len(line) or line[idx] != opener:
        return idx
    depth = 0
    while idx < len(line):
        char = line[idx]
        if char == opener:
            depth += 1
        elif char == closer:
            # Rust 的 -> 中的 '>' 不参与泛型配对
            if closer == ">" and idx > 0 and line[idx - 1] == "-":
                pass
            else:
                depth -= 1
                if depth == 0:
                    return idx + 1
        idx += 1
    return idx


def _name_regex(name):
    """构造函数名匹配片段，兼容以 $ 或 ~ 开头的名字。"""
    escaped = _escape_name(name)
    left = r"\b" if (name[0].isalnum() or name[0] == "_") else ""
    right = r"\b" if (name[-1].isalnum() or name[-1] == "_") else ""
    return left + escaped + right


def _locate_params(line, name, language):
    """定位函数名的参数列表 '('，返回 (名字所在前缀结束位置, '(' 下标)。"""
    escaped = _escape_name(name)
    if language in ("Rust", "Swift"):
        keyword = "fn" if language == "Rust" else "func"
        match = re.search(r"\b" + keyword + r"\s+" + escaped + r"\b", line)
        if not match:
            return None
        idx = _skip_bracket_group(line, match.end(), "<", ">")
        while idx < len(line) and line[idx].isspace():
            idx += 1
        if idx < len(line) and line[idx] == "(":
            return match.start(), idx
        return None
    if language == "Go":
        match = re.search(
            r"\bfunc\s+(?:\([^)]*\)\s*)?" + escaped + r"\b", line
        )
        if not match:
            return None
        idx = _skip_bracket_group(line, match.end(), "[", "]")
        while idx < len(line) and line[idx].isspace():
            idx += 1
        if idx < len(line) and line[idx] == "(":
            return match.start(), idx
        return None
    match = re.search(_name_regex(name) + r"\s*\(", line)
    if not match:
        return None
    return match.start(), match.end() - 1


def _find_code_token(text, start, end, token):
    """在 start..end 范围内查找 token，跳过注释和字符串。"""
    idx = start
    state = None
    while idx < end:
        char = text[idx]
        nxt = text[idx + 1] if idx + 1 < end else ""
        if state == "block":
            if char == "*" and nxt == "/":
                state = None
                idx += 1
        elif state == "line":
            if char == "\n":
                state = None
        elif state == "str":
            if char == "\\":
                idx += 1
            elif char == '"':
                state = None
        elif state == "chr":
            if char == "\\":
                idx += 1
            elif char == "'":
                state = None
        else:
            if char == "/" and nxt == "*":
                state = "block"
                idx += 1
            elif char == "/" and nxt == "/":
                state = "line"
                idx += 1
            elif char == '"':
                state = "str"
            elif char == "'":
                state = "chr"
            elif text.startswith(token, idx):
                return idx
        idx += 1
    return -1


def _extract_expression_body(text, close_idx, language, start_idx):
    """提取 Kotlin/C# 的单行表达式函数体。"""
    if language not in ("Kotlin", "C#"):
        return None
    line_end = text.find("\n", close_idx)
    if line_end < 0:
        line_end = len(text)
    brace = _find_code_token(text, close_idx + 1, line_end, "{")

    if language == "Kotlin":
        equal = _find_code_token(text, close_idx + 1, line_end, "=")
        if equal < 0 or text.startswith("==", equal):
            return None
        if brace >= 0 and brace < equal:
            return None
        end = line_end
    else:
        arrow = _find_code_token(text, close_idx + 1, line_end, "=>")
        if arrow < 0:
            return None
        if brace >= 0 and brace < arrow:
            return None
        end = _find_code_token(text, arrow, min(len(text), arrow + MAX_BODY_SCAN), ";")
        if end < 0:
            end = line_end
        else:
            end += 1

    implementation = text[:end].strip()
    if not implementation:
        return None
    end_line = start_idx + implementation.count("\n") + 1
    return implementation, implementation, start_idx + 1, end_line


def extract_brace_function(lines, line_idx, name, language):
    """提取带花括号语言的函数签名与完整实现。"""
    line = lines[line_idx]
    located = _locate_params(line, name, language)
    if located is None:
        return None
    match_start, open_in_line = located
    prefix = line[:match_start]

    required = REQUIRED_KEYWORD.get(language)
    if required and not re.search(required, line[:open_in_line]):
        return None
    if not _prefix_is_code(prefix):
        return None
    code_prefix = _strip_comments(prefix).rstrip()
    if _CONTROL_PREFIX_RE.search(code_prefix) or _NEW_PREFIX_RE.search(code_prefix):
        return None

    start_idx = _signature_start(lines, line_idx)
    end_idx = min(len(lines), line_idx + MAX_CONTEXT_LINES)
    text = "".join(lines[start_idx:end_idx])
    offset = sum(len(lines[i]) for i in range(start_idx, line_idx))
    open_idx = offset + open_in_line
    if open_idx >= len(text) or text[open_idx] != "(":
        return None

    close_idx = _matching_paren(text, open_idx)
    if close_idx < 0:
        return None

    expression_body = _extract_expression_body(text, close_idx, language, start_idx)
    if expression_body:
        return expression_body

    # 形如 int (*foo(void))(int) 的定义，参数列表后还有一个 ")" 需要跳过
    fp_declarator = bool(re.search(r"\(\s*\*+\s*(?:const\s+)?$", prefix))
    body_idx = _body_open(text, close_idx + 1, allow_group_close=fp_declarator)
    if body_idx < 0:
        return None
    if _in_block_comment(lines, line_idx):
        return None

    close_body = _matching_brace(text, body_idx)
    if close_body < 0 and end_idx < len(lines):
        # 函数体超出上下文窗口，扩展到文件末尾重新匹配
        text = "".join(lines[start_idx:])
        close_body = _matching_brace(text, body_idx)
    if close_body < 0:
        return None

    signature = text[:body_idx].strip().replace("\\\n", " ")
    implementation = text[: close_body + 1].strip()
    if not signature or not implementation:
        return None
    end_line = start_idx + implementation.count("\n") + 1
    return signature, implementation, start_idx + 1, end_line


# ---------------------------------------------------------------------------
# Python 源码解析
# ---------------------------------------------------------------------------


def _indent_width(line):
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 8 - (width % 8)
        else:
            break
    return width


def _find_python_colon(text, idx):
    """从参数列表之后寻找签名末尾的冒号，跳过注释和字符串。"""
    limit = min(len(text), idx + 2000)
    state = None
    while idx < limit:
        char = text[idx]
        if state == "str":
            if char == "\\":
                idx += 1
            elif char == '"':
                state = None
        elif state == "chr":
            if char == "\\":
                idx += 1
            elif char == "'":
                state = None
        else:
            if char == "#":
                newline = text.find("\n", idx)
                if newline < 0:
                    return -1
                idx = newline + 1
                continue
            if char == '"':
                state = "str"
            elif char == "'":
                state = "chr"
            elif char == ":":
                return idx
        idx += 1
    return -1


def _python_decorator_start(lines, line_idx):
    start = line_idx
    idx = line_idx - 1
    while idx >= 0 and lines[idx].strip().startswith("@"):
        start = idx
        idx -= 1
    return start


def extract_python_function(lines, line_idx, name):
    """提取 Python 函数签名与完整实现（包含装饰器）。"""
    line = lines[line_idx]
    match = re.match(
        r"^([ \t]*)(?:async\s+)?def\s+" + re.escape(name) + r"\s*\(", line
    )
    if not match:
        return None

    def_indent = _indent_width(line)
    end_idx = min(len(lines), line_idx + MAX_CONTEXT_LINES)
    text = "".join(lines[line_idx:end_idx])
    open_idx = match.end() - 1
    close_idx = _matching_paren(text, open_idx)
    if close_idx < 0:
        return None
    colon_idx = _find_python_colon(text, close_idx + 1)
    if colon_idx < 0:
        return None

    signature = text[: colon_idx + 1].strip()
    start_idx = _python_decorator_start(lines, line_idx)
    same_line = text[colon_idx + 1 :].split("\n", 1)[0]
    if same_line.strip() and not same_line.strip().startswith("#"):
        implementation = "".join(lines[start_idx : line_idx + 1]).rstrip()
        return signature, implementation, start_idx + 1, line_idx + 1

    # 多行签名时，函数体从冒号所在行的下一行开始
    colon_line_idx = line_idx + text[:colon_idx].count("\n")
    body_end = colon_line_idx
    idx = colon_line_idx + 1
    while idx < len(lines):
        current = lines[idx]
        if not current.strip():
            idx += 1
            continue
        indent = _indent_width(current)
        if current.strip().startswith("#"):
            if indent > def_indent:
                body_end = idx
            idx += 1
            continue
        if indent <= def_indent:
            break
        body_end = idx
        idx += 1

    implementation = "".join(lines[start_idx : body_end + 1]).rstrip()
    return signature, implementation, start_idx + 1, body_end + 1


def extract_function(lines, line_idx, name, language):
    if language == "Python":
        return extract_python_function(lines, line_idx, name)
    return extract_brace_function(lines, line_idx, name, language)


# ---------------------------------------------------------------------------
# 变量定义解析
# ---------------------------------------------------------------------------

_TRIPLE_QUOTE_LANGUAGES = {"Python", "Kotlin", "Scala", "Swift", "Dart"}
_SEMICOLON_REQUIRED_LANGUAGES = {"C/C++", "Objective-C", "Java", "C#"}

# 这些语言的变量声明可以由关键字直接引入，不必写类型。
_KEYWORD_DECLARATIONS = {
    "JavaScript": {"const", "let", "var"},
    "TypeScript": {"const", "let", "var"},
    "Kotlin": {"val", "var"},
    "Scala": {"val", "var"},
    "Swift": {"let", "var"},
    "Dart": {"var", "final", "const", "late"},
    "Go": {"var", "const"},
    "Rust": {"let", "static", "const"},
}

_ACCESS_MODIFIERS = {
    "public",
    "private",
    "protected",
    "internal",
    "fileprivate",
    "open",
    "package",
}
_JS_FIELD_MODIFIERS = {
    "public",
    "private",
    "protected",
    "static",
    "readonly",
    "declare",
    "abstract",
    "override",
    "accessor",
    "async",
}

# 出现在语句开头时基本可以断定不是变量声明。
_NON_DECLARATION_START = {
    "if",
    "elif",
    "else",
    "for",
    "foreach",
    "while",
    "switch",
    "case",
    "catch",
    "return",
    "throw",
    "new",
    "delete",
    "sizeof",
    "typeof",
    "alignof",
    "decltype",
    "using",
    "typedef",
    "namespace",
    "import",
    "package",
    "include",
    "from",
    "require",
    "with",
    "assert",
    "raise",
    "yield",
    "await",
    "del",
    "print",
    "goto",
    "do",
    "try",
    "finally",
    "match",
    "guard",
    "defer",
    "lock",
    "synchronized",
}

# 这些词单独出现在变量名前时只是修饰符/关键字，不是类型。
_NON_TYPE_TAIL_WORDS = _NON_DECLARATION_START | _ACCESS_MODIFIERS | {
    "static",
    "extern",
    "const",
    "volatile",
    "register",
    "mutable",
    "inline",
    "virtual",
    "explicit",
    "friend",
    "final",
    "readonly",
    "abstract",
    "sealed",
    "override",
    "lateinit",
    "lazy",
    "let",
    "var",
    "val",
    "class",
    "struct",
    "union",
    "enum",
    "interface",
    "trait",
    "protocol",
    "extension",
    "template",
    "type",
    "record",
    "object",
    "impl",
    "pub",
    "function",
    "func",
    "fn",
    "fun",
    "def",
    "lambda",
}


def _apostrophe_starts_literal(text, idx, language):
    """区分 Rust/C++ 的生命周期、数字分隔符与真正的字符字面量。"""
    if language == "Rust":
        nxt = text[idx + 1] if idx + 1 < len(text) else ""
        if not nxt:
            return False
        if nxt == "\\":
            return True
        if nxt.isalnum() or nxt == "_":
            return idx + 2 < len(text) and text[idx + 2] == "'"
        return True
    if language == "C/C++":
        previous = text[idx - 1] if idx else ""
        nxt = text[idx + 1] if idx + 1 < len(text) else ""
        if (
            previous in "0123456789abcdefABCDEF"
            and nxt in "0123456789abcdefABCDEF"
        ):
            # C++14 数字分隔符，例如 1'000'000。
            return False
    return True


def _mask_line(line, language):
    """把一行中的字符串和注释替换成空格，保留字符位置。"""
    chars = list(line)
    length = len(line)
    python = language == "Python"
    idx = 0
    while idx < length:
        char = line[idx]
        if python and char == "#":
            for pos in range(idx, length):
                chars[pos] = " "
            break
        if not python and char == "/" and idx + 1 < length:
            nxt = line[idx + 1]
            if nxt == "/":
                for pos in range(idx, length):
                    chars[pos] = " "
                break
            if nxt == "*":
                end = line.find("*/", idx + 2)
                if end < 0:
                    for pos in range(idx, length):
                        chars[pos] = " "
                    break
                for pos in range(idx, end + 2):
                    chars[pos] = " "
                idx = end + 2
                continue
        if char in ('"', "'", "`"):
            if char == "'" and not _apostrophe_starts_literal(
                line, idx, language
            ):
                idx += 1
                continue
            if (
                language in _TRIPLE_QUOTE_LANGUAGES
                and line.startswith(char * 3, idx)
            ):
                end = line.find(char * 3, idx + 3)
                if end < 0:
                    for pos in range(idx, length):
                        chars[pos] = " "
                    break
                for pos in range(idx, end + 3):
                    chars[pos] = " "
                idx = end + 3
                continue
            pos = idx + 1
            while pos < length:
                if line[pos] == "\\":
                    pos += 2
                    continue
                if line[pos] == char:
                    pos += 1
                    break
                pos += 1
            for fill in range(idx, min(pos, length)):
                chars[fill] = " "
            idx = pos
            continue
        idx += 1
    return "".join(chars)


def _line_needs_continuation(text, newline_idx, language):
    """判断换行后是否仍属于同一个声明语句。"""
    idx = newline_idx - 1
    while idx >= 0 and text[idx] in " \t\r":
        idx -= 1
    if idx < 0:
        return False
    if text[idx] == "\\":
        return True
    tail = text[max(0, newline_idx - 4) : newline_idx].rstrip()
    operators = ("=", "+", "-", "*", "/", "%", "&", "|", "^", "?", ":", "=>", "->", "<", ">", ".")
    if tail.endswith(operators):
        return True
    # 逗号在 JS/TS 的多声明和 Python 的多目标赋值里可能续行；
    # 在 Rust/Go/Kotlin 等语言里，行尾逗号通常只是字段/参数的结束。
    if language in ("JavaScript", "TypeScript", "Python") and tail.endswith(","):
        return True
    nxt = newline_idx + 1
    while nxt < len(text) and text[nxt] in " \t\r":
        nxt += 1
    return nxt < len(text) and text[nxt] in ".+-*/%&|^?:([{"


def _statement_end(text, language):
    """返回声明语句结束位置；用于变量定义的多行初始值/列表。"""
    length = len(text)
    python = language == "Python"
    semicolon_required = language in _SEMICOLON_REQUIRED_LANGUAGES
    state = None  # None / "line" / "block" / "string" / "triple" / "backtick"
    quote = ""
    depth = 0
    line_comment_start = -1
    idx = 0
    while idx < length:
        char = text[idx]
        nxt = text[idx + 1] if idx + 1 < length else ""

        if state == "block":
            if char == "*" and nxt == "/":
                state = None
                idx += 2
            else:
                idx += 1
            continue
        if state == "line":
            if char == "\n":
                comment_end = line_comment_start if line_comment_start >= 0 else idx
                state = None
                line_comment_start = -1
                if depth == 0 and not semicolon_required:
                    return comment_end
                idx += 1
                continue
            idx += 1
            continue
        if state == "triple":
            if text.startswith(quote * 3, idx):
                state = None
                idx += 3
            else:
                idx += 1
            continue
        if state == "string":
            if char == "\\":
                idx += 2
                continue
            if char == quote:
                state = None
            idx += 1
            continue
        if state == "backtick":
            if char == "`":
                state = None
            idx += 1
            continue

        if python and char == "#":
            state = "line"
            if depth == 0:
                line_comment_start = idx
            idx += 1
            continue
        if not python and char == "/" and nxt == "/":
            state = "line"
            if depth == 0:
                line_comment_start = idx
            idx += 2
            continue
        if not python and char == "/" and nxt == "*":
            state = "block"
            idx += 2
            continue
        if char in ('"', "'", "`"):
            if char == "'" and not _apostrophe_starts_literal(
                text, idx, language
            ):
                idx += 1
                continue
            if char == "`" and language in ("Go", "JavaScript", "TypeScript"):
                state = "backtick"
                idx += 1
                continue
            if language in _TRIPLE_QUOTE_LANGUAGES and text.startswith(
                char * 3, idx
            ):
                state = "triple"
                quote = char
                idx += 3
                continue
            state = "string"
            quote = char
            idx += 1
            continue
        if char in "([{":
            depth += 1
            idx += 1
            continue
        if char in ")]}":
            if depth > 0:
                depth -= 1
            elif char == "}":
                return idx
            idx += 1
            continue
        if char == ";" and depth == 0:
            return idx + 1
        if char == "\n" and depth == 0:
            if semicolon_required:
                idx += 1
                continue
            if _line_needs_continuation(text, idx, language):
                idx += 1
                continue
            return line_comment_start if line_comment_start >= 0 else idx
        idx += 1

    # 文件最后一行可能没有换行符；行尾语言在括号平衡时仍然是一条完整语句。
    if not semicolon_required and depth == 0:
        if state == "line" and line_comment_start >= 0:
            return line_comment_start
        return length
    return -1


def _collect_statement(lines, start_idx, language, start_offset=0):
    """收集从 start_idx/start_offset 开始的声明语句。"""
    end_idx = min(len(lines), start_idx + MAX_CONTEXT_LINES)
    full_text = "".join(lines[start_idx:end_idx])
    text = full_text[start_offset:]
    end = _statement_end(text, language)
    if end < 0 and end_idx < len(lines):
        full_text = "".join(lines[start_idx:])
        text = full_text[start_offset:]
        end = _statement_end(text, language)
    if end < 0:
        return None
    implementation = text[:end].strip()
    if not implementation:
        return None
    start_line = start_idx + full_text[:start_offset].count("\n") + 1
    end_line = start_line + implementation.count("\n")
    return implementation, start_line, end_line


def _variable_signature(implementation, name):
    """变量卡片顶部的声明摘要：取首个物理行，必要时补到出现变量名。"""
    parts = implementation.splitlines()
    if not parts:
        return ""
    signature = parts[0].strip()
    idx = 1
    while name not in signature and idx < len(parts) and idx < 4:
        signature = f"{signature} {parts[idx].strip()}"
        idx += 1
    signature = " ".join(signature.split())
    if len(signature) > 400:
        signature = signature[:397].rstrip() + "..."
    return signature


def _scope_line_kind(code, language):
    """粗略判断一行在作用域上属于函数、类型、命名空间还是普通代码块。"""
    stripped = code.strip()
    if not stripped:
        return "block"
    if stripped.startswith((")", "]", "}")):
        # Allman 风格的 "{" 行以及 "}) {" / "};" 等收尾行不是作用域声明。
        return "skip"
    if re.fullmatch(r"(?:public|private|protected|internal)\s*:", stripped):
        return "skip"
    if re.match(r"^@(?:interface|implementation|protocol)\b", stripped):
        return "class"
    if re.match(r"^[-+]\s*\(", stripped):
        return "function"
    if re.search(r"\b(?:interface|trait|protocol|extension|enum)\b", stripped):
        return "type"
    if re.match(
        r"^(?:export\s+)?(?:declare\s+)?type\s+[A-Za-z_$][\w$]*\s*(?:<[^>]*>)?\s*=",
        stripped,
    ):
        return "type"
    if re.search(
        r"\b(?:class|struct|union|object|record)\b", stripped
    ) or re.search(r"\bimpl\b", stripped):
        return "class"
    if re.search(r"\bnamespace\b", stripped):
        return "namespace"
    if re.search(r"\b(?:function|func|fn|fun|def)\b", stripped):
        return "function"
    if re.match(
        r"^(?:if|elif|else|for|foreach|while|switch|catch|do|try|finally|"
        r"when|guard|defer|with|match|loop|repeat|unless|using|lock|"
        r"synchronized)\b",
        stripped,
    ):
        return "block"
    if re.search(r"=>\s*\{?\s*$", stripped):
        return "function"
    if re.search(r"\)\s*(?:(?:->|:)\s*[^;{=]+)?\s*\{?\s*$", stripped):
        if not stripped.endswith(";"):
            return "function"
    return "block"


def _scope_kind(lines, line_idx, language):
    """根据缩进和上层声明判断变量作用域。

    返回值：global / local / member / type。
    """
    if line_idx <= 0:
        return "global"
    indent = _indent_width(lines[line_idx])
    if indent == 0:
        return "global"
    target_indent = indent
    idx = line_idx - 1
    while idx >= 0:
        raw = lines[idx]
        code = _mask_line(raw, language).strip()
        if not code:
            idx -= 1
            continue
        if code.startswith(("@", "#", "//", "/*", "*")) and not re.match(
            r"^@(?:interface|implementation|protocol)\b", code
        ):
            idx -= 1
            continue
        if language != "Python" and _in_block_comment(lines, idx):
            idx -= 1
            continue
        if language == "Python":
            in_string, _depth = _python_context_before(lines, idx)
            if in_string:
                idx -= 1
                continue
        if code in ("{", "};", "});") or re.fullmatch(
            r"[)\]}]+\s*[;,]?", code
        ):
            idx -= 1
            continue
        current_indent = _indent_width(raw)
        if current_indent >= target_indent:
            idx -= 1
            continue
        kind = _scope_line_kind(code, language)
        if kind == "skip":
            idx -= 1
            continue
        if kind in ("function", "class", "type"):
            return {
                "function": "local",
                "class": "member",
                "type": "type",
            }[kind]
        if kind == "namespace":
            return "global"
        # 控制语句、访问修饰符等：向上退一层继续找。
        target_indent = current_indent
        idx -= 1
    return "global"


def _display_scope(location, language):
    if location == "type":
        return "type"
    if location == "member":
        return "class" if language == "Python" else "member"
    if location == "local":
        return "local"
    return "global"


def _statement_prefix(masked, match_start):
    """返回同一语句中位于变量名之前的内容及其起始偏移。"""
    start = 0
    depth = 0
    for idx in range(match_start):
        char = masked[idx]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            start = idx + 1
    return masked[start:match_start], start


def _has_top_level_equals(text):
    depth = 0
    for idx, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "=" and depth == 0:
            before = text[idx - 1] if idx else ""
            after = text[idx + 1] if idx + 1 < len(text) else ""
            if before in "=!<>" or after in "=>":
                continue
            return True
    return False


def _unmatched_opening_depth(text):
    depth = 0
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
    return depth


def _next_significant(text, start):
    idx = start
    while idx < len(text) and text[idx] in " \t\r\n":
        idx += 1
    return text[idx] if idx < len(text) else ""


def _name_matches(masked, name):
    try:
        regex = re.compile(_name_regex(name))
    except re.error:
        return []
    return list(regex.finditer(masked))


def _keyword_declaration(prefix, language):
    keywords = _KEYWORD_DECLARATIONS.get(language)
    if not keywords:
        return False
    for match in reversed(list(re.finditer(r"[A-Za-z_$][\w$]*", prefix))):
        if match.group() not in keywords:
            continue
        before = prefix[: match.start()]
        between = prefix[match.end() :]
        words = re.findall(r"[A-Za-z_$][\w$]*", before)
        if any(word in _NON_DECLARATION_START for word in words):
            continue
        if "{" in before and any(
            word in ("class", "struct", "interface", "object")
            for word in words
        ):
            # 同一行 class/struct 大括号内的局部声明不能算作同一层语句。
            continue
        if _has_top_level_equals(before) or _has_top_level_equals(between):
            continue
        return True
    return False


def _typed_prefix_ok(prefix, language):
    stripped = prefix.strip()
    if not stripped:
        return False
    if re.match(
        r"^@(?:property|synthesize|dynamic|interface|implementation|"
        r"protocol|end|optional|required|public|private|protected|"
        r"package)\b",
        stripped,
    ):
        return False
    if _has_top_level_equals(stripped):
        return False
    if stripped.endswith((".", "->", "=", "+", "-", "/", "%", "!", "|", "^", "&&", "||", "(", "[", "{")):
        return False
    last_char = stripped[-1]
    if not (last_char.isalnum() or last_char in "_$>]?*&,"):
        return False
    last_word = re.search(r"([A-Za-z_$][\w$]*)\s*[,]*\s*$", stripped)
    if last_word:
        word = last_word.group(1)
        if word in _NON_TYPE_TAIL_WORDS and not (
            word == "var" and language in ("Java", "C#")
        ):
            return False
    first_word = re.match(
        r"\s*(?:@[A-Za-z_$][\w$.]*(?:\([^)]*\))?\s*)?([A-Za-z_$][\w$]*)",
        stripped,
    )
    if first_word and first_word.group(1) in _NON_DECLARATION_START:
        return False
    if not re.search(r"[A-Za-z_$][\w$]*", stripped):
        return False
    return True


def _qualified_type_prefix(prefix, language):
    """C++ 静态成员定义 int Foo::name = 0; 中取回类型部分。"""
    if language not in ("C/C++", "Objective-C"):
        return None
    match = re.match(
        r"^(.*?)((?:[A-Za-z_$][\w$]*\s*::\s*)+)$", prefix, re.S
    )
    if not match:
        return None
    type_part = match.group(1)
    return type_part if type_part.strip() else None


def _target_after_ok(after, language, keyword=False):
    if not after:
        return True
    if after in "=:;,[])}":
        return True
    if after == "{" and language in ("C/C++", "Objective-C"):
        return True
    if after in ("*", "&") and not keyword:
        return True
    if keyword and language == "Go" and (after.isalnum() or after in "_*["):
        return True
    if keyword and language in ("Kotlin", "Scala") and (
        after.isalnum() or after in "_$"
    ):
        return True
    return False


def _extract_name_matches(lines, line_idx, name, language):
    """通用声明解析：支持带类型的 C 风格语言和关键字声明。"""
    line = lines[line_idx]
    masked_line = _mask_line(line, language)
    matches = _name_matches(masked_line, name)
    if not matches:
        return None
    if language not in ("Python",) and _in_block_comment(lines, line_idx):
        return None

    for match in matches:
        start_idx = line_idx
        lead = masked_line[: match.start()].strip()
        if not lead:
            start_idx = _declaration_start(lines, line_idx, language)
        combined = "".join(lines[start_idx : line_idx + 1])
        masked_combined = "".join(
            _mask_line(part, language) for part in lines[start_idx : line_idx + 1]
        )
        offset = sum(
            len(lines[index]) for index in range(start_idx, line_idx)
        ) + match.start()
        statement_prefix, prefix_start = _statement_prefix(
            masked_combined, offset
        )
        after_pos = offset + len(match.group(0))
        after = _next_significant(masked_combined, after_pos)
        prefix_text = statement_prefix.strip()
        if not prefix_text or prefix_text.startswith("#"):
            continue

        keyword = _keyword_declaration(statement_prefix, language)

        # 目标位于上一行遗留的圆括号/方括号中时，通常是函数参数或调用参数；
        # Kotlin 主构造器里的 val/var 属性是例外，单独放行。
        outer_depth = _unmatched_opening_depth(masked_combined[:prefix_start])
        if outer_depth > 0 and not (keyword and language == "Kotlin"):
            continue

        qualified_prefix = None
        if keyword:
            if language in ("JavaScript", "TypeScript"):
                unclosed_pattern = (
                    statement_prefix.count("{") > statement_prefix.count("}")
                    or statement_prefix.count("[")
                    > statement_prefix.count("]")
                )
                if (
                    after == ":"
                    and unclosed_pattern
                ):
                    # const { key: alias } 中 key 是属性名，不是新变量。
                    continue
                if (
                    unclosed_pattern
                    and statement_prefix.rstrip().endswith("=")
                ):
                    # const { key = name } 中 name 位于默认值表达式里。
                    continue
            if not _target_after_ok(after, language, keyword=True):
                continue
        else:
            qualified_prefix = _qualified_type_prefix(
                statement_prefix, language
            )
            type_prefix = qualified_prefix or statement_prefix
            if _unmatched_opening_depth(type_prefix) > 0:
                continue
            if not _typed_prefix_ok(type_prefix, language):
                continue
            if not _target_after_ok(after, language, keyword=False):
                continue
        if language == "C#" and re.match(r"\s*=>", masked_combined[after_pos:]):
            # 表达式体属性不是变量定义。
            continue

        location = _scope_kind(lines, line_idx, language)
        scope = (
            "member"
            if qualified_prefix is not None
            else _display_scope(location, language)
        )
        collected = _collect_statement(
            lines, start_idx, language, prefix_start
        )
        if collected is None:
            continue
        implementation, start_line, end_line = collected
        signature = _variable_signature(implementation, name)
        return signature, implementation, start_line, end_line, scope
    return None


def _declaration_start(lines, line_idx, language):
    """变量名位于续行时，向上包含类型/关键字所在行。"""
    start = line_idx
    previous = line_idx - 1
    while previous >= 0 and line_idx - previous <= 3:
        code = _mask_line(lines[previous], language).strip()
        if not code:
            break
        if code.startswith(("#", "//", "/*", "*", "@")):
            break
        if code.endswith((";", "{", "}", "*/", ",")):
            break
        if _scope_line_kind(code, language) in ("function", "class", "type"):
            break
        start = previous
        previous -= 1
    return start


def _python_target_matches(node, name):
    if isinstance(node, ast.Name):
        return node.id == name
    if isinstance(node, ast.Starred):
        return _python_target_matches(node.value, name)
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_python_target_matches(element, name) for element in node.elts)
    if isinstance(node, ast.Attribute):
        return (
            node.attr == name
            and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
        )
    return False


def _python_target_is_attribute(node, name):
    if isinstance(node, ast.Attribute):
        return (
            node.attr == name
            and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
        )
    if isinstance(node, ast.Starred):
        return _python_target_is_attribute(node.value, name)
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(
            _python_target_is_attribute(element, name) for element in node.elts
        )
    return False


def _python_definition(tree, name):
    """返回 (是否定义, 是否为 self/cls 属性)。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _python_target_matches(target, name):
                    return True, _python_target_is_attribute(target, name)
        elif isinstance(node, ast.AnnAssign):
            if _python_target_matches(node.target, name):
                return True, _python_target_is_attribute(node.target, name)
        elif isinstance(node, ast.NamedExpr):
            if _python_target_matches(node.target, name):
                return True, _python_target_is_attribute(node.target, name)
    return False, False


_PYTHON_CONTEXT_CACHE = {}
_PYTHON_CONTEXT_CACHE_LIMIT = 8


def _python_contexts(lines):
    """一次扫描整份 Python 文件，缓存每行行首的字符串/括号状态。"""
    key = id(lines)
    cached = _PYTHON_CONTEXT_CACHE.get(key)
    if cached is not None and cached[0] is lines and cached[1] == len(lines):
        return cached[2]

    in_string = []
    depths = []
    fence = None
    depth = 0
    for line in lines:
        in_string.append(fence is not None)
        depths.append(depth)
        pos = 0
        while pos < len(line):
            if fence is not None:
                end = line.find(fence, pos)
                if end < 0:
                    break
                pos = end + 3
                fence = None
                continue
            char = line[pos]
            if char == "#":
                break
            if char in ('"', "'"):
                if line.startswith(char * 3, pos):
                    fence = char * 3
                    pos += 3
                    continue
                pos += 1
                while pos < len(line):
                    if line[pos] == "\\":
                        pos += 2
                        continue
                    if line[pos] == char:
                        pos += 1
                        break
                    pos += 1
                continue
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth = max(0, depth - 1)
            pos += 1

    contexts = list(zip(in_string, depths))
    if len(_PYTHON_CONTEXT_CACHE) >= _PYTHON_CONTEXT_CACHE_LIMIT:
        oldest = next(iter(_PYTHON_CONTEXT_CACHE))
        _PYTHON_CONTEXT_CACHE.pop(oldest, None)
    _PYTHON_CONTEXT_CACHE[key] = (lines, len(lines), contexts)
    return contexts


def _python_context_before(lines, line_idx):
    """返回 (是否位于多行字符串内, 行首之前未闭合的括号深度)。"""
    contexts = _python_contexts(lines)
    return contexts[line_idx]


def _extract_python_variable(lines, line_idx, name, language):
    in_string, outer_depth = _python_context_before(lines, line_idx)
    if in_string or outer_depth > 0:
        # 多行字符串或括号表达式内部不是新的赋值语句。
        return None
    masked = _mask_line(lines[line_idx], language)
    if not _name_matches(masked, name):
        return None
    collected = _collect_statement(lines, line_idx, language)
    if collected is None:
        return None
    implementation, start_line, end_line = collected
    try:
        tree = ast.parse(textwrap.dedent(implementation), mode="exec")
    except (SyntaxError, ValueError):
        return None
    defined, is_attribute = _python_definition(tree, name)
    if not defined:
        return None
    if is_attribute:
        scope = "instance"
    else:
        scope = _display_scope(
            _scope_kind(lines, line_idx, language), language
        )
    signature = _variable_signature(implementation, name)
    return signature, implementation, start_line, end_line, scope


def _in_go_group_declaration(lines, line_idx):
    masked = _mask_line(lines[line_idx], "Go")
    if not masked.strip():
        return False
    indent = _indent_width(lines[line_idx])
    for idx in range(line_idx - 1, max(-1, line_idx - 200), -1):
        raw = lines[idx]
        code = _mask_line(raw, "Go").strip()
        if not code or code.startswith(("//", "/*", "*")):
            continue
        if _indent_width(raw) < indent:
            return bool(re.match(r"^(?:var|const)\s*\(\s*$", code))
    return False


def _go_short_declaration_after(masked, start):
    depth = 0
    idx = start
    while idx < len(masked):
        char = masked[idx]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0:
            if masked.startswith(":=", idx):
                return True
            if char == ";":
                return False
        idx += 1
    return False


def _extract_go_variable(lines, line_idx, name, language):
    grouped = _in_go_group_declaration(lines, line_idx)
    if not grouped:
        found = _extract_name_matches(lines, line_idx, name, language)
        if found:
            return found

    masked = _mask_line(lines[line_idx], language)
    matches = _name_matches(masked, name)
    if not matches:
        return None
    location = _scope_kind(lines, line_idx, language)

    for match in matches:
        prefix, prefix_start = _statement_prefix(masked, match.start())
        prefix_text = prefix.strip()
        after = _next_significant(masked, match.end())

        # Go 的短变量声明：name := value，允许 a, name := ...
        short_prefix_ok = not prefix_text or re.fullmatch(
            r"[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*\s*,\s*", prefix_text
        )
        if (
            short_prefix_ok
            and after != "="
            and _go_short_declaration_after(masked, match.end())
            and after in (",", ":")
        ):
            collected = _collect_statement(
                lines, line_idx, language, prefix_start
            )
            if collected:
                implementation, start_line, end_line = collected
                scope = _display_scope(location, language)
                return (
                    _variable_signature(implementation, name),
                    implementation,
                    start_line,
                    end_line,
                    scope,
                )

        # 结构体字段：name type / name, other type
        group_prefix_ok = not prefix_text or re.fullmatch(
            r"[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*\s*,\s*", prefix_text
        )
        if (
            not grouped
            and location == "member"
            and group_prefix_ok
            and after not in ("(", "=", ".")
            and (after.isalnum() or after in "_*[")
        ):
            collected = _collect_statement(
                lines, line_idx, language, prefix_start
            )
            if collected:
                implementation, start_line, end_line = collected
                return (
                    _variable_signature(implementation, name),
                    implementation,
                    start_line,
                    end_line,
                    "member",
                )

        # 分组 var/const 块中的一行：name type = value
        if (
            grouped
            and group_prefix_ok
            and after not in ("(", ".", ":=")
        ):
            collected = _collect_statement(
                lines, line_idx, language, prefix_start
            )
            if collected:
                implementation, start_line, end_line = collected
                return (
                    _variable_signature(implementation, name),
                    implementation,
                    start_line,
                    end_line,
                    _display_scope(location, language),
                )
    return None


def _extract_rust_variable(lines, line_idx, name, language):
    found = _extract_name_matches(lines, line_idx, name, language)
    if found:
        return found

    masked = _mask_line(lines[line_idx], language)
    if _scope_kind(lines, line_idx, language) != "member":
        return None
    for match in _name_matches(masked, name):
        prefix, prefix_start = _statement_prefix(masked, match.start())
        prefix_text = prefix.strip()
        if prefix_text and not re.fullmatch(
            r"(?:pub(?:\s*\([^)]*\))?\s*)*", prefix_text
        ):
            continue
        after = _next_significant(masked, match.end())
        if after != ":":
            continue
        collected = _collect_statement(lines, line_idx, language, prefix_start)
        if collected:
            implementation, start_line, end_line = collected
            return (
                _variable_signature(implementation, name),
                implementation,
                start_line,
                end_line,
                "member",
            )
    return None


def _inline_class_context(masked, position):
    """检测同一行内 class {...} 中的字段，返回 member/type/None。"""
    before = masked[:position]
    last = None
    for match in re.finditer(r"\b(class|interface|struct)\b", before):
        last = match
    if last is None:
        return None
    rest = before[last.end() :]
    brace = rest.find("{")
    if brace < 0 or "}" in rest[brace:]:
        return None
    return "type" if last.group(1) == "interface" else "member"


def _extract_js_class_field(lines, line_idx, name, language):
    if language not in ("JavaScript", "TypeScript"):
        return None
    masked = _mask_line(lines[line_idx], language)
    base_location = _scope_kind(lines, line_idx, language)
    for match in _name_matches(masked, name):
        location = base_location
        if location != "member":
            location = _inline_class_context(masked, match.start())
        if location != "member":
            continue
        prefix, prefix_start = _statement_prefix(masked, match.start())
        statement_start = prefix_start
        field_prefix = prefix
        if base_location != "member" and "{" in prefix:
            cut = prefix.rfind("{") + 1
            field_prefix = prefix[cut:]
            statement_start = prefix_start + cut
            previous_semicolon = field_prefix.rfind(";")
            if previous_semicolon >= 0:
                field_prefix = field_prefix[previous_semicolon + 1 :]
                statement_start += previous_semicolon + 1
        prefix_text = field_prefix.strip()
        if prefix_text:
            cleaned = prefix_text.replace("#", "", 1).strip()
            if cleaned:
                words = re.findall(r"[A-Za-z_$][\w$]*", cleaned)
                if any(word not in _JS_FIELD_MODIFIERS for word in words):
                    continue
                if re.sub(r"[A-Za-z_$][\w$]*", "", cleaned).strip(" \t#"):
                    continue
        after_pos = match.end()
        after = _next_significant(masked, after_pos)
        if after in ("?", "!"):
            after_pos = masked.find(after, after_pos) + 1
            after = _next_significant(masked, after_pos)
        if after not in ("", "=", ":", ";"):
            continue
        collected = _collect_statement(
            lines, line_idx, language, statement_start
        )
        if collected:
            implementation, start_line, end_line = collected
            return (
                _variable_signature(implementation, name),
                implementation,
                start_line,
                end_line,
                "member",
            )
    return None


def extract_variable(lines, line_idx, name, language):
    """提取变量/常量定义。

    返回 (signature, implementation, start_line, end_line, scope)；
    scope 为 global / local / member / class / instance / type。
    """
    if language == "Python":
        return _extract_python_variable(lines, line_idx, name, language)
    if language == "Go":
        return _extract_go_variable(lines, line_idx, name, language)
    if language == "Rust":
        return _extract_rust_variable(lines, line_idx, name, language)
    if language in ("JavaScript", "TypeScript"):
        found = _extract_name_matches(lines, line_idx, name, language)
        if found:
            return found
        found = _extract_js_class_field(lines, line_idx, name, language)
        if found:
            return found
        return None
    return _extract_name_matches(lines, line_idx, name, language)


# ---------------------------------------------------------------------------
# 搜索实现
# ---------------------------------------------------------------------------


class _FileCache:
    """缓存最近读取的文件内容，避免同一文件的候选行重复读盘。"""

    def __init__(self):
        self.path = None
        self.lines = []

    def get(self, path):
        if path != self.path:
            self.path = path
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    self.lines = handle.readlines()
            except OSError:
                self.lines = []
        return self.lines


def _escape_name(name):
    return "".join(
        "\\" + char if char in r"\.^$*+?{}[]|()" else char for char in name
    )


def _build_pattern(name):
    escaped = _escape_name(name)
    plain = _name_regex(name)
    return (
        plain
        + r"\s*\("
        + r"|\bfn\s+"
        + escaped
        + r"\b"
        + r"|\bfunc\s+(?:\([^)]*\)\s*)?"
        + escaped
        + r"\b"
    )


def _build_variable_pattern(name):
    """构造变量定义候选行的匹配表达式。

    这个表达式只负责召回候选行，最终是否为定义由 extract_variable()
    按语言和上下文确认。
    """
    word = _name_regex(name)
    return (
        word
        + r"\s*[!?]?\s*(?:=|:=|;|,|\[|:)"
        + r"|(?:^|[;{])\s*(?:[\w:<>\[\],.*&?]+\s+)+[*&]*"
        + word
        + r"|\b(?:const|let|var|val|static|final|readonly|lateinit|lazy|"
        + r"auto|extern|mutable|public|private|protected|internal)\b[^\n;]*"
        + word
        + r"|(?:^|[;{])\s*"
        + word
        + r"(?:[!?]?\s*[:=]|\s+[A-Za-z_*\[])"
    )


_BACKEND_LABELS = {"rg": "ripgrep", "grep": "grep", "python": "Python"}


def _choose_backend():
    """按速度选择后端：ripgrep > grep > 纯 Python。"""
    forced = os.environ.get("FUNC_FINDER_BACKEND", "").strip().lower()
    has_rg = shutil.which("rg") is not None
    has_grep = os.name != "nt" and shutil.which("grep") is not None
    if forced in ("rg", "ripgrep") and has_rg:
        return "rg"
    if forced == "grep" and has_grep:
        return "grep"
    if forced in ("python", "py"):
        return "python"
    if has_rg:
        return "rg"
    if has_grep:
        return "grep"
    return "python"


def _grep_pattern(name):
    """构造 GNU/BSD grep -E 兼容的函数名匹配表达式。"""
    escaped = _escape_name(name)
    start = r"(^|[^[:alnum:]_])"
    end = r"([^[:alnum:]_]|$)"
    return (
        start + escaped + r"[[:space:]]*\("
        + r"|" + start + r"fn[[:space:]]+" + escaped + end
        + r"|" + start + r"func[[:space:]]+(\([^)]*\)[[:space:]]*)?"
        + escaped + end
    )


def _grep_variable_pattern(name):
    """grep 后端使用较宽的变量候选匹配，交给提取阶段精确判断。"""
    escaped = _escape_name(name)
    start = r"(^|[^[:alnum:]_])"
    end = r"([^[:alnum:]_]|$)"
    return start + escaped + end


class SearchEngine:
    """与界面解耦的搜索实现，便于单独测试。"""

    def __init__(self, stop_event, progress_callback=None):
        self.stop_event = stop_event
        self.progress_callback = progress_callback
        self.proc = None
        self.scanned = 0

    def stop(self):
        self.stop_event.set()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def search(self, root, name, mode=SEARCH_MODE_FUNCTION):
        mode = (
            SEARCH_MODE_VARIABLE
            if mode == SEARCH_MODE_VARIABLE
            else SEARCH_MODE_FUNCTION
        )
        cache = _FileCache()
        for path, lineno, _line in self._iter_candidates(root, name, mode):
            if self.stop_event.is_set():
                return
            self.scanned += 1
            if self.progress_callback and self.scanned % 500 == 0:
                self.progress_callback(self.scanned)

            suffix = os.path.splitext(path)[1].lower()
            language = LANGUAGE_BY_SUFFIX.get(suffix)
            if not language:
                continue
            lines = cache.get(path)
            index = lineno - 1
            if index < 0 or index >= len(lines):
                continue

            if mode == SEARCH_MODE_VARIABLE:
                found = extract_variable(lines, index, name, language)
                if found:
                    signature, implementation, start_line, end_line, scope = found
                    yield SearchResult(
                        name=name,
                        path=path,
                        start_line=start_line,
                        end_line=end_line,
                        signature=signature,
                        implementation=implementation,
                        language=language,
                        kind=SEARCH_MODE_VARIABLE,
                        scope=scope,
                    )
                continue

            found = extract_function(lines, index, name, language)
            if found:
                signature, implementation, start_line, end_line = found
                yield SearchResult(
                    name=name,
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    signature=signature,
                    implementation=implementation,
                    language=language,
                    kind=SEARCH_MODE_FUNCTION,
                )

    @staticmethod
    def backend_name():
        return _BACKEND_LABELS[_choose_backend()]

    def _iter_candidates(self, target, name, mode=SEARCH_MODE_FUNCTION):
        backend = _choose_backend()
        if mode == SEARCH_MODE_VARIABLE:
            if backend == "rg":
                pattern = _build_variable_pattern(name)
            elif backend == "grep":
                pattern = None
            else:
                pattern = _build_variable_pattern(name)
        else:
            pattern = _build_pattern(name)

        if backend == "rg":
            yield from self._iter_with_rg(
                shutil.which("rg"), target, pattern
            )
        elif backend == "grep":
            yield from self._iter_with_grep(
                shutil.which("grep"), target, name, mode
            )
        else:
            yield from self._iter_with_python(target, pattern)

    def _iter_with_rg(self, rg, target, pattern):
        globs = "*.{" + ",".join(
            suffix.lstrip(".") for suffix in sorted(LANGUAGE_BY_SUFFIX)
        ) + "}"
        cmd = [rg, "--no-config", "--no-messages", "--json", "--glob", globs]
        for excluded in EXCLUDE_DIRS:
            cmd += ["--glob", f"!**/{excluded}/**"]
        cmd += ["--regexp", pattern, "--", target]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError:
            yield from self._iter_with_python(target, pattern)
            return

        self.proc = proc
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if self.stop_event.is_set():
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "match":
                    continue
                data = event.get("data", {})
                path_text = data.get("path", {}).get("text")
                lines_text = data.get("lines", {}).get("text")
                lineno = data.get("line_number")
                if not path_text or lines_text is None or lineno is None:
                    continue
                yield path_text, lineno, lines_text
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.stdout.close()
            except OSError:
                pass
            proc.wait()
            self.proc = None

    def _iter_with_grep(
        self, grep, target, name, mode=SEARCH_MODE_FUNCTION
    ):
        cmd = [grep, "-r", "-n", "-I", "-E"]
        for suffix in sorted(LANGUAGE_BY_SUFFIX):
            cmd += ["--include", "*" + suffix]
        for excluded in EXCLUDE_DIRS:
            cmd += ["--exclude-dir", excluded]
        pattern = (
            _grep_variable_pattern(name)
            if mode == SEARCH_MODE_VARIABLE
            else _grep_pattern(name)
        )
        cmd += ["-e", pattern, "--", target]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError:
            fallback = (
                _build_variable_pattern(name)
                if mode == SEARCH_MODE_VARIABLE
                else _build_pattern(name)
            )
            yield from self._iter_with_python(target, fallback)
            return

        self.proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self.stop_event.is_set():
                    break
                parts = line.rstrip("\n").split(":", 2)
                if len(parts) != 3:
                    continue
                path, lineno_text, content = parts
                try:
                    lineno = int(lineno_text)
                except ValueError:
                    continue
                yield path, lineno, content
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.stdout.close()
            except OSError:
                pass
            proc.wait()
            self.proc = None

    def _iter_with_python(self, target, pattern):
        regex = re.compile(pattern)
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [
                name for name in dirnames if name not in SKIP_DIRS
            ]
            for filename in filenames:
                suffix = os.path.splitext(filename)[1].lower()
                if suffix not in LANGUAGE_BY_SUFFIX:
                    continue
                if self.stop_event.is_set():
                    return
                path = os.path.join(dirpath, filename)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        for lineno, line in enumerate(handle, 1):
                            if regex.search(line):
                                yield path, lineno, line
                except OSError:
                    continue


# ---------------------------------------------------------------------------
# 代码高亮
# ---------------------------------------------------------------------------


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document, function_name):
        super().__init__(document)
        self.keyword_format = QTextCharFormat()
        self.keyword_format.setForeground(QColor("#93c5fd"))
        self.keyword_format.setFontWeight(QFont.Weight.Bold)

        self.string_format = QTextCharFormat()
        self.string_format.setForeground(QColor("#86efac"))

        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(QColor("#94a3b8"))
        self.comment_format.setFontItalic(True)

        self.number_format = QTextCharFormat()
        self.number_format.setForeground(QColor("#fca5a5"))

        self.name_format = QTextCharFormat()
        self.name_format.setForeground(QColor("#fbbf24"))
        self.name_format.setFontWeight(QFont.Weight.Bold)

        escaped = re.escape(function_name)
        try:
            self.name_re = QRegularExpression(r"\b" + escaped + r"\b")
        except Exception:
            self.name_re = None

    def highlightBlock(self, text):
        covered = []
        iterator = _STRING_COMMENT_RE.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()
            token = match.captured(0)
            if token.startswith(("//", "#")):
                self.setFormat(start, length, self.comment_format)
            else:
                self.setFormat(start, length, self.string_format)
            covered.append((start, start + length))

        rules = [(_KEYWORD_RE, self.keyword_format), (_NUMBER_RE, self.number_format)]
        if self.name_re is not None:
            rules.append((self.name_re, self.name_format))
        for regex, fmt in rules:
            iterator = regex.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                start = match.capturedStart()
                length = match.capturedLength()
                end = start + length
                if any(start < covered_end and end > covered_start
                       for covered_start, covered_end in covered):
                    continue
                self.setFormat(start, length, fmt)

        self._highlight_block_comment(text)

    def _highlight_block_comment(self, text):
        if self.previousBlockState() == 1:
            end = text.find("*/")
            if end < 0:
                self.setFormat(0, len(text), self.comment_format)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, end + 2, self.comment_format)
            start = end + 2
            self.setCurrentBlockState(0)
        else:
            start = 0

        while True:
            begin = text.find("/*", start)
            if begin < 0:
                break
            end = text.find("*/", begin + 2)
            if end < 0:
                self.setFormat(begin, len(text) - begin, self.comment_format)
                self.setCurrentBlockState(1)
                break
            self.setFormat(begin, end + 2 - begin, self.comment_format)
            start = end + 2


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------


LIGHT_STYLE = """
QMainWindow, QWidget#central {
    background-color: #f4f6fa;
}
QLabel {
    color: #1f2937;
}
QLabel#title {
    font-size: 24px;
    font-weight: bold;
    color: #0f172a;
}
QLabel#subtitle {
    font-size: 13px;
    color: #64748b;
}
QLabel#sectionTitle {
    font-size: 15px;
    font-weight: bold;
    color: #0f172a;
}
QLabel#fieldLabel {
    font-size: 13px;
    font-weight: bold;
    color: #334155;
}
QLabel#muted {
    font-size: 12px;
    color: #64748b;
}
QLabel#status {
    font-size: 12px;
    color: #475569;
}
QLabel#status[state="error"] {
    color: #dc2626;
}
QLabel#pathStatus {
    font-size: 12px;
    color: #64748b;
}
QLabel#pathStatus[state="error"] {
    color: #dc2626;
}
QLabel#hint {
    font-size: 12px;
    color: #94a3b8;
}
QLabel#empty {
    font-size: 13px;
    color: #94a3b8;
    padding: 48px 16px;
}
QFrame#panel {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
}
QFrame#card {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
}
QLabel#funcName {
    font-size: 16px;
    font-weight: bold;
    color: #0f172a;
}
QLabel#badge {
    background-color: #e0ecff;
    color: #1d4ed8;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: bold;
}
QLabel#scopeBadge {
    background-color: #ecfdf5;
    color: #047857;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: bold;
}
QLabel#pathLabel {
    font-size: 12px;
    color: #64748b;
}
QFrame#signatureBox {
    background-color: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QLabel#signatureText {
    background: transparent;
    color: #334155;
}
QLineEdit {
    background-color: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 8px 10px;
    color: #111827;
    selection-background-color: #bfdbfe;
}
QLineEdit:focus {
    border: 1px solid #2563eb;
}
QLineEdit:disabled {
    background-color: #f9fafb;
    color: #9ca3af;
}
QPushButton {
    background-color: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 8px 16px;
    color: #374151;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #f8fafc;
}
QPushButton:pressed {
    background-color: #f1f5f9;
}
QPushButton:disabled {
    background-color: #f9fafb;
    border-color: #e5e7eb;
    color: #9ca3af;
}
QPushButton#primary {
    background-color: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
}
QPushButton#primary:hover {
    background-color: #1d4ed8;
    border-color: #1d4ed8;
}
QPushButton#primary:disabled {
    background-color: #bfdbfe;
    border-color: #bfdbfe;
    color: #eff6ff;
}
QPushButton#stop {
    background-color: #fff5f5;
    border-color: #fecaca;
    color: #b91c1c;
}
QPushButton#stop:hover {
    background-color: #fee2e2;
}
QPushButton#stop:disabled {
    background-color: #f9fafb;
    border-color: #e5e7eb;
    color: #9ca3af;
}
QPushButton#ghost {
    background-color: transparent;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 6px 12px;
    color: #475569;
    font-size: 12px;
    font-weight: bold;
}
QPushButton#ghost:hover {
    background-color: #f8fafc;
}
QPushButton#segment {
    background-color: transparent;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 3px 10px;
    color: #64748b;
    font-size: 12px;
    font-weight: bold;
}
QPushButton#segment:hover {
    background-color: #f8fafc;
}
QPushButton#segment:checked {
    background-color: #dbeafe;
    border-color: #bfdbfe;
    color: #1d4ed8;
}
QPushButton#segment:checked:hover {
    background-color: #dbeafe;
}
QPushButton#segment:disabled {
    background-color: #f9fafb;
    border-color: #e5e7eb;
    color: #9ca3af;
}
QPushButton#segment:checked:disabled {
    background-color: #eff6ff;
    border-color: #dbeafe;
    color: #93c5fd;
}
QPushButton#small {
    padding: 3px 10px;
    font-size: 12px;
    border-radius: 6px;
}
QToolButton#copyButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px;
}
QToolButton#copyButton:hover {
    background-color: #f1f5f9;
    border-color: #e2e8f0;
}
QToolButton#copyButton:pressed {
    background-color: #e2e8f0;
}
QToolButton#copyButtonDark {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px;
}
QToolButton#copyButtonDark:hover {
    background-color: #1e293b;
    border-color: #334155;
}
QToolButton#copyButtonDark:pressed {
    background-color: #0f172a;
}
QFrame#codeBox {
    background-color: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
}
QLabel#codeLabel {
    color: #94a3b8;
    font-size: 12px;
    font-weight: bold;
}
QPlainTextEdit#code {
    background-color: #0f172a;
    color: #e2e8f0;
    border: none;
    padding: 0;
    selection-background-color: #334155;
}
QScrollArea {
    background: transparent;
    border: none;
}
QWidget#resultsContainer {
    background: transparent;
}
QProgressBar {
    background-color: #e5e7eb;
    border: none;
    border-radius: 3px;
    height: 6px;
}
QProgressBar::chunk {
    background-color: #2563eb;
    border-radius: 3px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #cbd5e1;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #94a3b8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #cbd5e1;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QPlainTextEdit#code QScrollBar:vertical {
    background: #0f172a;
}
QPlainTextEdit#code QScrollBar:horizontal {
    background: #0f172a;
}
QPlainTextEdit#code QScrollBar::handle:vertical,
QPlainTextEdit#code QScrollBar::handle:horizontal {
    background: #334155;
}
"""

DARK_STYLE = """
QMainWindow, QWidget#central {
    background-color: #0b1120;
}
QLabel {
    color: #e2e8f0;
}
QLabel#title {
    color: #f8fafc;
}
QLabel#subtitle {
    color: #94a3b8;
}
QLabel#sectionTitle {
    color: #f1f5f9;
}
QLabel#fieldLabel {
    color: #cbd5e1;
}
QLabel#muted {
    color: #94a3b8;
}
QLabel#status {
    color: #94a3b8;
}
QLabel#status[state="error"] {
    color: #f87171;
}
QLabel#pathStatus {
    color: #94a3b8;
}
QLabel#pathStatus[state="error"] {
    color: #f87171;
}
QLabel#hint {
    color: #64748b;
}
QLabel#empty {
    color: #64748b;
}
QFrame#panel {
    background-color: #111827;
    border-color: #1f2937;
}
QFrame#card {
    background-color: #111827;
    border-color: #1f2937;
}
QLabel#funcName {
    color: #f8fafc;
}
QLabel#badge {
    background-color: #1e3a8a;
    color: #bfdbfe;
}
QLabel#scopeBadge {
    background-color: #064e3b;
    color: #a7f3d0;
}
QLabel#pathLabel {
    color: #94a3b8;
}
QFrame#signatureBox {
    background-color: #0f172a;
    border-color: #1e293b;
}
QLabel#signatureText {
    color: #cbd5e1;
}
QLineEdit {
    background-color: #0f172a;
    border-color: #334155;
    color: #f1f5f9;
    selection-background-color: #1d4ed8;
}
QLineEdit:focus {
    border-color: #60a5fa;
}
QLineEdit:disabled {
    background-color: #0b1120;
    color: #64748b;
}
QPushButton {
    background-color: #1f2937;
    border-color: #334155;
    color: #e5e7eb;
}
QPushButton:hover {
    background-color: #273449;
}
QPushButton:pressed {
    background-color: #0f172a;
}
QPushButton:disabled {
    background-color: #111827;
    border-color: #1f2937;
    color: #64748b;
}
QPushButton#primary {
    background-color: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
}
QPushButton#primary:hover {
    background-color: #3b82f6;
    border-color: #3b82f6;
}
QPushButton#primary:disabled {
    background-color: #1e3a8a;
    border-color: #1e3a8a;
    color: #93c5fd;
}
QPushButton#stop {
    background-color: #2a1215;
    border-color: #7f1d1d;
    color: #fca5a5;
}
QPushButton#stop:hover {
    background-color: #3f1a1f;
}
QPushButton#stop:disabled {
    background-color: #111827;
    border-color: #1f2937;
    color: #64748b;
}
QPushButton#ghost {
    background-color: transparent;
    border-color: #334155;
    color: #cbd5e1;
}
QPushButton#ghost:hover {
    background-color: #1f2937;
}
QPushButton#segment {
    background-color: transparent;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 3px 10px;
    color: #94a3b8;
    font-size: 12px;
    font-weight: bold;
}
QPushButton#segment:hover {
    background-color: #1e293b;
}
QPushButton#segment:checked {
    background-color: #1e3a8a;
    border-color: #1d4ed8;
    color: #bfdbfe;
}
QPushButton#segment:checked:hover {
    background-color: #1e3a8a;
}
QPushButton#segment:disabled {
    background-color: #111827;
    border-color: #1f2937;
    color: #64748b;
}
QPushButton#segment:checked:disabled {
    background-color: #172554;
    border-color: #1e3a8a;
    color: #93c5fd;
}
QToolButton#copyButton:hover {
    background-color: #1e293b;
    border-color: #334155;
}
QToolButton#copyButton:pressed {
    background-color: #0f172a;
}
QProgressBar {
    background-color: #1f2937;
}
QProgressBar::chunk {
    background-color: #3b82f6;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #334155;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #475569;
}
"""


class SearchWorker(QThread):
    results_ready = Signal(object)
    progress = Signal(int)
    finished_ok = Signal(bool, int, float)
    failed = Signal(str)

    BATCH_SIZE = 256
    BATCH_INTERVAL = 0.10

    def __init__(
        self,
        root,
        name,
        parent=None,
        mode=SEARCH_MODE_FUNCTION,
    ):
        super().__init__(parent)
        self.root = root
        self.name = name
        self.mode = (
            SEARCH_MODE_VARIABLE
            if mode == SEARCH_MODE_VARIABLE
            else SEARCH_MODE_FUNCTION
        )
        self.stop_event = threading.Event()
        self.engine = SearchEngine(self.stop_event, self._emit_progress)

    def _emit_progress(self, scanned):
        self.progress.emit(scanned)

    def stop(self):
        self.engine.stop()

    def run(self):
        started = time.monotonic()
        pending = []
        last_flush = started

        def flush():
            nonlocal pending, last_flush
            if not pending:
                return
            batch = pending
            pending = []
            last_flush = time.monotonic()
            self.results_ready.emit(batch)

        try:
            for result in self.engine.search(self.root, self.name, self.mode):
                if self.stop_event.is_set():
                    break
                pending.append(result)
                now = time.monotonic()
                if (
                    len(pending) >= self.BATCH_SIZE
                    or now - last_flush >= self.BATCH_INTERVAL
                ):
                    flush()
            flush()
            self.finished_ok.emit(
                self.stop_event.is_set(),
                self.engine.scanned,
                time.monotonic() - started,
            )
        except Exception as exc:  # 后台线程兜底，避免搜索静默失败
            flush()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def result_to_text(result):
    """返回可复制的函数实现或变量定义代码。"""
    return result.implementation


_ICON_CACHE = {}
_CODE_FONT = None


def _get_code_font():
    """缓存等宽字体，避免每张结果卡片重复查询字体数据库。"""
    global _CODE_FONT
    if _CODE_FONT is None:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        _CODE_FONT = font
    return _CODE_FONT


def _make_copy_icon(copied, color="#64748b"):
    """绘制 GitHub 风格的复制/已复制图标（结果缓存复用）。"""
    key = (bool(copied), color)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if copied:
        pen = QPen(
            QColor("#22c55e"),
            3.0,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.drawLine(QPointF(8.5, 16.5), QPointF(13.5, 21.5))
        painter.drawLine(QPointF(13.5, 21.5), QPointF(23.5, 10.5))
    else:
        pen = QPen(
            QColor(color),
            2.2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(11, 10, 12, 15), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(8, 7, 12, 15), 2.5, 2.5)
    painter.end()
    icon = QIcon(pixmap)
    _ICON_CACHE[key] = icon
    return icon


class CopyButton(QToolButton):
    """只显示图标的复制按钮，复制成功后变成对勾。"""

    def __init__(self, lang="zh", tooltip_key="copy_tooltip",
                 icon_color="#64748b", object_name="copyButton", parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 30)
        self.setIconSize(QSize(18, 18))
        self._lang = lang
        self._tooltip_key = tooltip_key
        self._copied = False
        self._copy_icon = _make_copy_icon(False, icon_color)
        self._check_icon = _make_copy_icon(True)
        self.setIcon(self._copy_icon)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.reset_state)
        self._update_tooltip()

    def set_language(self, lang):
        self._lang = lang
        self._update_tooltip()

    def show_copied(self):
        self._copied = True
        self.setIcon(self._check_icon)
        self._update_tooltip()
        self._timer.start(1500)

    def reset_state(self):
        self._copied = False
        self.setIcon(self._copy_icon)
        self._update_tooltip()

    def _update_tooltip(self):
        key = "copied" if self._copied else self._tooltip_key
        self.setToolTip(tr(self._lang, key))
        self.setAccessibleName(self.toolTip())


class ResultCard(QFrame):
    def __init__(self, result, base_dir, lang="zh", parent=None):
        super().__init__(parent)
        self.result = result
        self.lang = lang
        self.kind = getattr(result, "kind", SEARCH_MODE_FUNCTION)
        self.scope = getattr(result, "scope", "")
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(15, 23, 42, 26))
        self.setGraphicsEffect(shadow)

        code_font = _get_code_font()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        name_label = QLabel(result.name)
        name_label.setObjectName("funcName")
        badge = QLabel(result.language)
        badge.setObjectName("badge")
        self.scope_label = QLabel()
        self.scope_label.setObjectName("scopeBadge")
        self.scope_label.setVisible(
            self.kind == SEARCH_MODE_VARIABLE and bool(self.scope)
        )
        self.range_label = QLabel()
        self.range_label.setObjectName("muted")
        header.addWidget(name_label)
        header.addWidget(badge)
        if self.kind == SEARCH_MODE_VARIABLE:
            header.addWidget(self.scope_label)
        header.addStretch(1)
        header.addWidget(self.range_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        try:
            relative = os.path.relpath(result.path, base_dir)
        except ValueError:
            relative = result.path
        if relative.startswith(".."):
            relative = result.path
        path_label = QLabel(f"{relative}:{result.start_line}")
        path_label.setObjectName("pathLabel")
        path_label.setToolTip(result.path)
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(path_label)

        signature = " ".join(result.signature.split())
        sig_box = QFrame()
        sig_box.setObjectName("signatureBox")
        sig_layout = QHBoxLayout(sig_box)
        sig_layout.setContentsMargins(10, 4, 6, 4)
        sig_layout.setSpacing(6)
        self.signature_label = QLabel(signature)
        self.signature_label.setObjectName("signatureText")
        self.signature_label.setFont(code_font)
        self.signature_label.setWordWrap(True)
        self.signature_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.sig_copy_btn = CopyButton(
            lang,
            tooltip_key=(
                "copy_variable_signature_tooltip"
                if self.kind == SEARCH_MODE_VARIABLE
                else "copy_signature_tooltip"
            ),
        )
        self.sig_copy_btn.clicked.connect(self.copy_signature)
        sig_layout.addWidget(self.signature_label, 1)
        sig_layout.addWidget(
            self.sig_copy_btn, 0, Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(sig_box)

        code_box = QFrame()
        code_box.setObjectName("codeBox")
        code_layout = QVBoxLayout(code_box)
        code_layout.setContentsMargins(10, 6, 6, 6)
        code_layout.setSpacing(4)

        code_header = QHBoxLayout()
        code_header.setSpacing(6)
        self.impl_label = QLabel()
        self.impl_label.setObjectName("codeLabel")
        self.copy_btn = CopyButton(
            lang,
            tooltip_key=(
                "copy_variable_tooltip"
                if self.kind == SEARCH_MODE_VARIABLE
                else "copy_tooltip"
            ),
            icon_color="#94a3b8",
            object_name="copyButtonDark",
        )
        self.copy_btn.clicked.connect(self.copy_result)
        code_header.addWidget(
            self.impl_label, 0, Qt.AlignmentFlag.AlignVCenter
        )
        code_header.addStretch(1)
        code_header.addWidget(
            self.copy_btn, 0, Qt.AlignmentFlag.AlignVCenter
        )
        code_layout.addLayout(code_header)

        code = QPlainTextEdit()
        code.setObjectName("code")
        code.setReadOnly(True)
        code.setFont(code_font)
        code.setPlainText(result.implementation)
        code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        code.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        code.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        code.setTabStopDistance(4 * code.fontMetrics().horizontalAdvance(" "))
        code.document().setDocumentMargin(0)
        line_count = result.implementation.count("\n") + 1
        height = line_count * code.fontMetrics().lineSpacing() + 26
        code.setFixedHeight(max(66, min(height, 400)))
        code_layout.addWidget(code)
        layout.addWidget(code_box)

        self.highlighter = CodeHighlighter(code.document(), result.name)
        self.apply_language(lang)

    def apply_language(self, lang):
        self.lang = lang
        self.range_label.setText(
            tr(lang, "lines_range", start=self.result.start_line,
               end=self.result.end_line)
        )
        if self.kind == SEARCH_MODE_VARIABLE:
            self.impl_label.setText(tr(lang, "variable_definition"))
            if self.scope:
                self.scope_label.setText(
                    tr(lang, f"scope_{self.scope}")
                )
        else:
            self.impl_label.setText(tr(lang, "implementation"))
        self.copy_btn.set_language(lang)
        self.sig_copy_btn.set_language(lang)

    def copy_signature(self):
        QApplication.clipboard().setText(
            " ".join(self.result.signature.split())
        )
        self.sig_copy_btn.show_copied()

    def copy_result(self):
        QApplication.clipboard().setText(result_to_text(self.result))
        self.copy_btn.show_copied()


class ResultsView(QScrollArea):
    """结果列表：只创建视口附近的卡片，结果再多也不会拖慢界面。

    所有搜索结果都保存在 ``results`` 中，滚动条按估算/实测高度映射到
    完整结果列表；实际存在的 QWidget 数量始终保持在 10 个左右。
    """

    GAP = 12
    RIGHT_MARGIN = 8
    BUFFER_BEFORE = 3
    BUFFER_AFTER = 4
    RESIZE_DELAY_MS = 60

    def __init__(self, lang="zh", parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.lang = lang
        self.base_dir = ""
        self.results = []
        self.heights = []
        self.offsets = [0]
        self.cards = {}
        self.start = 0
        self.end = 0
        self._layout_width = 0
        self._updating = False
        self._empty_visible = False
        self._code_metrics = QFontMetrics(_get_code_font())
        self._line_spacing = self._code_metrics.lineSpacing()

        self.container = QWidget()
        self.container.setObjectName("resultsContainer")
        self.setWidget(self.container)

        self.empty_label = QLabel(self.container)
        self.empty_label.setObjectName("empty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh)

        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(50)
        self._layout_timer.timeout.connect(self._apply_pending_layout)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(self.RESIZE_DELAY_MS)
        self._resize_timer.timeout.connect(lambda: self._refresh(force=True))

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # -- 对外接口 -----------------------------------------------------------

    def set_empty_text(self, text):
        self.empty_label.setText(text)
        if self._empty_visible and not self.results:
            self._position_empty()

    def set_empty_visible(self, visible):
        self._empty_visible = bool(visible)
        self._sync_empty()

    def apply_language(self, lang):
        self.lang = lang
        for card in self.cards.values():
            card.apply_language(lang)

    def clear(self):
        self.results = []
        self.heights = []
        self.offsets = [0]
        self._layout_timer.stop()
        self._clear_cards()
        self._container_height()
        self.verticalScrollBar().setValue(0)
        self._sync_empty()

    def append_results(self, results, base_dir=None):
        """批量追加结果；只在需要时创建卡片。"""
        if not results:
            return
        if base_dir is not None:
            self.base_dir = base_dir
        for result in results:
            self.results.append(result)
            height = self._estimate_height(result)
            self.heights.append(height)
            self.offsets.append(self.offsets[-1] + height + self.GAP)
        self._sync_empty()
        self._schedule_layout()
        self._schedule_refresh()

    def flush_layout(self):
        """搜索结束后立即同步滚动范围。"""
        self._layout_timer.stop()
        self._container_height()
        self._schedule_refresh()

    def live_card_count(self):
        return len(self.cards)

    # -- 高度与滚动映射 -----------------------------------------------------

    def _content_width(self):
        return max(120, self.viewport().width() - self.RIGHT_MARGIN)

    def _estimate_height(self, result):
        """不创建 QWidget 的近似高度，用于尚未实例化的结果。"""
        line_count = result.implementation.count("\n") + 1
        raw_code_height = line_count * self._line_spacing + 26
        code_height = max(66, min(raw_code_height, 400))
        if raw_code_height >= 400:
            height = code_height + 180
        elif code_height <= 66:
            height = code_height + 202
        else:
            height = code_height + 197

        width = self._content_width()
        if raw_code_height < 400 and width > 180:
            signature = " ".join(result.signature.split())
            available = max(40, width - 82)
            if self._code_metrics.horizontalAdvance(signature) > available:
                rect = self._code_metrics.boundingRect(
                    0,
                    0,
                    available,
                    10000,
                    Qt.TextFlag.TextWordWrap,
                    signature,
                )
                height += max(0, rect.height() - self._line_spacing)
        return height

    def _recompute_offsets(self):
        offsets = [0]
        total = 0
        for height in self.heights:
            total += height + self.GAP
            offsets.append(total)
        self.offsets = offsets

    def _container_height(self):
        total = self.offsets[-1] if self.offsets else 0
        height = max(total, self.viewport().height())
        self.container.setMinimumHeight(height)
        width = self.viewport().width()
        if self.container.width() != width or self.container.height() != height:
            self.container.resize(width, height)
        self._position_empty()

    def _index_at(self, y):
        if not self.results:
            return 0
        index = bisect.bisect_right(self.offsets, y) - 1
        return max(0, min(len(self.results) - 1, index))

    # -- 卡片实例化 ---------------------------------------------------------

    def _clear_cards(self):
        for card in self.cards.values():
            card.hide()
            card.deleteLater()
        self.cards = {}
        self.start = 0
        self.end = 0

    def _measure_card(self, card, width):
        card.setGeometry(0, 0, width, 100)
        card.ensurePolished()
        height = max(card.sizeHint().height(), card.heightForWidth(width))
        return max(66, height)

    def _position_cards(self):
        width = self._content_width()
        for index, card in self.cards.items():
            card.setGeometry(
                0, self.offsets[index], width, self.heights[index]
            )

    def _position_empty(self):
        if not self._empty_visible or self.results:
            return
        self.empty_label.setGeometry(
            0,
            0,
            self._content_width(),
            max(1, self.viewport().height()),
        )

    def _sync_empty(self):
        visible = self._empty_visible and not self.results
        self.empty_label.setVisible(visible)
        if visible:
            self._position_empty()

    # -- 刷新视口 -----------------------------------------------------------

    def _on_scroll(self, _value):
        if not self._updating:
            self._schedule_refresh()

    def _schedule_refresh(self):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _schedule_layout(self):
        if not self._layout_timer.isActive():
            self._layout_timer.start()

    def _apply_pending_layout(self):
        self._container_height()
        self._schedule_refresh()

    def _refresh(self, force=False):
        if self._updating:
            return
        if not self.results:
            self._clear_cards()
            self._container_height()
            self._sync_empty()
            return

        self.empty_label.hide()
        bar = self.verticalScrollBar()
        y = bar.value()
        viewport_height = self.viewport().height()
        first = self._index_at(y)
        last = self._index_at(y + max(1, viewport_height - 1))
        start = max(0, first - self.BUFFER_BEFORE)
        end = min(len(self.results), last + 1 + self.BUFFER_AFTER)
        if (
            not force
            and self.cards
            and start >= self.start
            and end <= self.end
            and first - self.start >= 1
            and self.end - (last + 1) >= 2
        ):
            return

        anchor = first
        anchor_delta = y - self.offsets[anchor]
        width = self._content_width()
        remeasure = width != self._layout_width
        self._updating = True
        self.setUpdatesEnabled(False)
        try:
            old_cards = self.cards
            new_cards = {}
            heights_changed = False
            for index in range(start, end):
                card = old_cards.pop(index, None)
                if card is None:
                    card = ResultCard(
                        self.results[index],
                        self.base_dir,
                        self.lang,
                        self.container,
                    )
                    card.show()
                    height = self._measure_card(card, width)
                    if abs(height - self.heights[index]) > 1:
                        self.heights[index] = height
                        heights_changed = True
                elif remeasure:
                    height = self._measure_card(card, width)
                    if abs(height - self.heights[index]) > 1:
                        self.heights[index] = height
                        heights_changed = True
                new_cards[index] = card

            for card in old_cards.values():
                card.hide()
                card.deleteLater()

            if heights_changed:
                self._recompute_offsets()
            self.cards = new_cards
            self.start, self.end = start, end
            self._layout_width = width
            self._container_height()
            for index, card in self.cards.items():
                card.setGeometry(
                    0, self.offsets[index], width, self.heights[index]
                )
            target = self.offsets[anchor] + anchor_delta
            bar.setValue(
                max(0, min(target, self.verticalScrollBar().maximum()))
            )
        finally:
            self.setUpdatesEnabled(True)
            self._updating = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()
        self._position_cards()
        self._position_empty()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1040, 780)
        self.setMinimumSize(860, 620)

        icon = _load_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.settings = QSettings()
        self.theme = self.settings.value("theme", "light", type=str)
        if self.theme not in ("light", "dark"):
            self.theme = "light"
        self.lang = self.settings.value("language", "zh", type=str)
        if self.lang not in ("zh", "en"):
            self.lang = "zh"
        self.search_mode = self.settings.value(
            "search_mode", SEARCH_MODE_FUNCTION, type=str
        )
        if self.search_mode not in SEARCH_MODES:
            self.search_mode = SEARCH_MODE_FUNCTION
        self.current_mode = self.search_mode
        self.worker = None
        self.workers = set()
        self.search_generation = 0
        self.result_count = 0
        self.base_dir = ""
        self.search_base_dir = ""
        self.current_name = ""
        self.has_searched = False
        self.empty_state = "initial"
        self.status_state = ("status_initial", {}, False)
        self.path_state = ("path_not_configured", {}, True)
        self._last_scanned = 0
        self._stop_requested = False
        self._applied_theme = None
        self._pending_results = []

        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(80)
        self._stats_timer.timeout.connect(self._flush_stats)

        self._results_timer = QTimer(self)
        self._results_timer.setSingleShot(True)
        self._results_timer.setInterval(80)
        self._results_timer.timeout.connect(self._flush_pending_results)

        self._build_ui()
        self._load_settings()

    # -- 界面构建 -----------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(28, 22, 28, 20)
        outer.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_label = QLabel()
        self.title_label.setObjectName("title")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("subtitle")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        header_row.addLayout(title_box)
        header_row.addStretch(1)
        self.lang_btn = QPushButton()
        self.lang_btn.setObjectName("ghost")
        self.lang_btn.setFixedSize(56, 32)
        self.lang_btn.clicked.connect(self.toggle_language)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ghost")
        self.theme_btn.setFixedSize(104, 32)
        self.theme_btn.clicked.connect(self.toggle_theme)
        header_row.addWidget(self.lang_btn, 0, Qt.AlignmentFlag.AlignTop)
        header_row.addWidget(self.theme_btn, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header_row)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(10)

        self.dir_label = QLabel()
        self.dir_label.setObjectName("fieldLabel")
        panel_layout.addWidget(self.dir_label)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setClearButtonEnabled(True)
        self.browse_btn = QPushButton()
        self.apply_btn = QPushButton()
        dir_row.addWidget(self.path_edit, 1)
        dir_row.addWidget(self.browse_btn)
        dir_row.addWidget(self.apply_btn)
        panel_layout.addLayout(dir_row)

        self.path_status = QLabel("")
        self.path_status.setObjectName("pathStatus")
        self.path_status.setWordWrap(True)
        panel_layout.addWidget(self.path_status)

        search_label_row = QHBoxLayout()
        search_label_row.setSpacing(6)
        self.search_label = QLabel()
        self.search_label.setObjectName("fieldLabel")
        self.func_mode_btn = QPushButton()
        self.func_mode_btn.setObjectName("segment")
        self.func_mode_btn.setCheckable(True)
        self.func_mode_btn.setAutoExclusive(True)
        self.func_mode_btn.setFixedHeight(26)
        self.func_mode_btn.setMinimumWidth(64)
        self.func_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.var_mode_btn = QPushButton()
        self.var_mode_btn.setObjectName("segment")
        self.var_mode_btn.setCheckable(True)
        self.var_mode_btn.setAutoExclusive(True)
        self.var_mode_btn.setFixedHeight(26)
        self.var_mode_btn.setMinimumWidth(64)
        self.var_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.func_mode_btn.setChecked(
            self.search_mode == SEARCH_MODE_FUNCTION
        )
        self.var_mode_btn.setChecked(
            self.search_mode == SEARCH_MODE_VARIABLE
        )
        search_label_row.addWidget(self.search_label)
        search_label_row.addStretch(1)
        search_label_row.addWidget(self.func_mode_btn)
        search_label_row.addWidget(self.var_mode_btn)
        panel_layout.addLayout(search_label_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_btn = QPushButton()
        self.search_btn.setObjectName("primary")
        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("stop")
        self.stop_btn.setEnabled(False)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_btn)
        search_row.addWidget(self.stop_btn)
        panel_layout.addLayout(search_row)

        self.hint_label = QLabel()
        self.hint_label.setObjectName("hint")
        self.hint_label.setWordWrap(True)
        panel_layout.addWidget(self.hint_label)

        outer.addWidget(panel)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(110)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.progress)
        outer.addLayout(status_row)

        results_header = QHBoxLayout()
        results_header.setSpacing(8)
        self.results_title_label = QLabel()
        self.results_title_label.setObjectName("sectionTitle")
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        self.copy_all_btn = QPushButton()
        self.copy_all_btn.setObjectName("small")
        self.copy_all_btn.setIcon(_make_copy_icon(False))
        self.copy_all_btn.setIconSize(QSize(16, 16))
        self.copy_all_btn.setEnabled(False)
        self.copy_all_btn.clicked.connect(self.copy_all_results)
        results_header.addWidget(self.results_title_label)
        results_header.addStretch(1)
        results_header.addWidget(self.count_label)
        results_header.addWidget(self.copy_all_btn)
        outer.addLayout(results_header)

        self.results_view = ResultsView(self.lang)
        outer.addWidget(self.results_view, 1)
        self.results_view.set_empty_visible(True)

        self.browse_btn.clicked.connect(self.browse_directory)
        self.apply_btn.clicked.connect(self.apply_directory)
        self.path_edit.returnPressed.connect(self.apply_directory)
        self.func_mode_btn.clicked.connect(
            lambda: self.set_search_mode(SEARCH_MODE_FUNCTION)
        )
        self.var_mode_btn.clicked.connect(
            lambda: self.set_search_mode(SEARCH_MODE_VARIABLE)
        )
        self.search_btn.clicked.connect(self.start_search)
        self.stop_btn.clicked.connect(self.stop_search)
        self.search_edit.returnPressed.connect(self.start_search)

        self.apply_theme()
        self.apply_language()

    def apply_theme(self):
        if self._applied_theme == self.theme:
            self._update_theme_button()
            return
        self._applied_theme = self.theme
        sheet = LIGHT_STYLE
        if self.theme == "dark":
            sheet += DARK_STYLE
        self.setUpdatesEnabled(False)
        try:
            self.setStyleSheet(sheet)
        finally:
            self.setUpdatesEnabled(True)
        self._update_theme_button()

    def _update_theme_button(self):
        key = "theme_light" if self.theme == "dark" else "theme_dark"
        self.theme_btn.setText(tr(self.lang, key))
        self.theme_btn.setToolTip(tr(self.lang, "theme_tooltip"))

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self.apply_theme()
        self.settings.setValue("theme", self.theme)

    def toggle_language(self):
        self.lang = "en" if self.lang == "zh" else "zh"
        self.settings.setValue("language", self.lang)
        self.apply_language()

    def apply_language(self):
        self.setWindowTitle(tr(self.lang, "app_title"))
        self.title_label.setText(tr(self.lang, "title"))
        self.subtitle_label.setText(tr(self.lang, "subtitle"))
        self.dir_label.setText(tr(self.lang, "code_dir"))
        self.path_edit.setPlaceholderText(tr(self.lang, "path_placeholder"))
        self.browse_btn.setText(tr(self.lang, "browse"))
        self.apply_btn.setText(tr(self.lang, "apply"))
        self._update_search_mode_texts()
        self.search_btn.setText(tr(self.lang, "search"))
        self.stop_btn.setText(tr(self.lang, "stop"))
        self._update_hint()
        self.results_title_label.setText(tr(self.lang, "results_title"))
        self.copy_all_btn.setText(tr(self.lang, "copy_all"))
        self.copy_all_btn.setToolTip(tr(self.lang, "copy_all_tooltip"))
        self.lang_btn.setText("中文" if self.lang == "en" else "EN")
        self.lang_btn.setToolTip(tr(self.lang, "lang_tooltip"))
        self._update_theme_button()
        self._update_count_label()
        self._update_empty_label()
        self._render_status()
        self._render_path_status()
        self.results_view.apply_language(self.lang)

    def set_search_mode(self, mode, persist=True):
        if mode not in SEARCH_MODES:
            return
        self.search_mode = mode
        self.func_mode_btn.setChecked(mode == SEARCH_MODE_FUNCTION)
        self.var_mode_btn.setChecked(mode == SEARCH_MODE_VARIABLE)
        if persist:
            self.settings.setValue("search_mode", mode)
        self._update_search_mode_texts()
        self._update_hint()
        self._update_empty_label()
        if self._empty_visible_now():
            self.results_view.set_empty_visible(True)
        self.search_edit.setFocus()

    def _empty_visible_now(self):
        return not self.results_view.results

    def _update_search_mode_texts(self):
        variable = self.search_mode == SEARCH_MODE_VARIABLE
        self.search_label.setText(
            tr(self.lang, "variable_name" if variable else "function_name")
        )
        self.search_edit.setPlaceholderText(
            tr(
                self.lang,
                "variable_placeholder" if variable else "search_placeholder",
            )
        )
        self.func_mode_btn.setText(tr(self.lang, "mode_function"))
        self.var_mode_btn.setText(tr(self.lang, "mode_variable"))
        self.func_mode_btn.setToolTip(tr(self.lang, "mode_function_tooltip"))
        self.var_mode_btn.setToolTip(tr(self.lang, "mode_variable_tooltip"))

    def _update_hint(self):
        hint_key = (
            "variable_hint"
            if self.search_mode == SEARCH_MODE_VARIABLE
            else "hint"
        )
        hint = tr(self.lang, hint_key)
        engine = SearchEngine.backend_name()
        if engine != "ripgrep":
            hint += "\n" + tr(self.lang, "hint_install_rg", engine=engine)
        if self.hint_label.text() != hint:
            self.hint_label.setText(hint)

    def _load_settings(self):
        path = self.settings.value("code_path", "", type=str) or ""
        self.path_edit.setText(path)
        if path and os.path.isdir(path):
            self.base_dir = path
            self._set_path_status("path_configured", path=path)
            self._set_status("status_ready")
        else:
            self._set_path_status("path_not_configured", error=True)
            self._set_status("status_initial")

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        self.search_edit.setFocus()

    # -- 设置与状态 ---------------------------------------------------------

    def _set_status(self, key, error=False, **kwargs):
        self.status_state = (key, kwargs, error)
        self._render_status()

    def _set_path_status(self, key, error=False, **kwargs):
        self.path_state = (key, kwargs, error)
        self._render_path_status()

    def _render_status(self):
        key, kwargs, error = self.status_state
        render_kwargs = dict(kwargs)
        kind_mode = render_kwargs.pop("kind_mode", None)
        if kind_mode is not None:
            kind_key = (
                "kind_variable"
                if kind_mode == SEARCH_MODE_VARIABLE
                else "kind_function"
            )
            render_kwargs["kind"] = tr(self.lang, kind_key)
        text = tr(self.lang, key, **render_kwargs)
        if self.status_label.text() != text:
            self.status_label.setText(text)
        self._set_label_state(self.status_label, error)

    def _render_path_status(self):
        key, kwargs, error = self.path_state
        text = tr(self.lang, key, **kwargs)
        if self.path_status.text() != text:
            self.path_status.setText(text)
        self._set_label_state(self.path_status, error)

    def _update_count_label(self):
        text = (
            results_count_text(self.lang, self.result_count)
            if self.has_searched
            else ""
        )
        if self.count_label.text() != text:
            self.count_label.setText(text)

    def _update_empty_label(self):
        variable = self.search_mode == SEARCH_MODE_VARIABLE
        key = {
            "initial": (
                "empty_initial_variable" if variable else "empty_initial"
            ),
            "no_results": (
                "empty_no_results_variable"
                if variable
                else "empty_no_results"
            ),
            "failed": "empty_failed",
        }.get(self.empty_state, "empty_initial")
        self.results_view.set_empty_text(tr(self.lang, key))

    def _schedule_stats(self):
        if not self._stats_timer.isActive():
            self._stats_timer.start()

    def _status_kind_kwargs(self):
        return {"kind_mode": self.current_mode}

    def _flush_stats(self):
        if self.worker is None or not self.worker.isRunning():
            return
        if self._stop_requested:
            return
        self._update_count_label()
        kind_kwargs = self._status_kind_kwargs()
        if self._last_scanned:
            self._set_status(
                "searching_progress",
                name=self.current_name,
                scanned=self._last_scanned,
                count=self.result_count,
                **kind_kwargs,
            )
        else:
            self._set_status(
                "searching_found",
                name=self.current_name,
                count=self.result_count,
                **kind_kwargs,
            )

    @staticmethod
    def _set_label_state(label, error):
        state = "error" if error else "normal"
        if label.property("state") == state:
            return
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)

    def _set_searching(self, active):
        # 窗口操作不因搜索而禁用，新搜索会取消旧搜索
        self.stop_btn.setEnabled(active)
        self.progress.setVisible(active)
        self.func_mode_btn.setEnabled(not active)
        self.var_mode_btn.setEnabled(not active)

    # -- 目录配置 -----------------------------------------------------------

    def browse_directory(self):
        start_dir = self.base_dir or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, tr(self.lang, "choose_dir_title"), start_dir
        )
        if path:
            self.path_edit.setText(path)
            self.apply_directory()

    def apply_directory(self):
        raw = self.path_edit.text().strip()
        if not raw:
            self._set_path_status("path_empty_error", error=True)
            self._set_status("status_not_configured", error=True)
            return
        path = Path(raw).expanduser()
        if not path.is_dir():
            self._set_path_status("path_missing_error", path=path, error=True)
            self._set_status("status_invalid_dir", error=True)
            return
        path = path.resolve()
        self.base_dir = str(path)
        self.path_edit.setText(self.base_dir)
        self.settings.setValue("code_path", self.base_dir)
        self._set_path_status("path_saved", path=self.base_dir)
        self._set_status("status_ready")

    # -- 搜索流程 -----------------------------------------------------------

    def start_search(self):
        variable = self.search_mode == SEARCH_MODE_VARIABLE
        name = self.search_edit.text().strip()
        if not name:
            self._set_status(
                "error_empty_variable_name" if variable else "error_empty_name",
                error=True,
            )
            return
        name_pattern = (
            r"[A-Za-z_$][A-Za-z0-9_$]*"
            if variable
            else r"~?[A-Za-z_$][A-Za-z0-9_$]*"
        )
        if not re.fullmatch(name_pattern, name):
            self._set_status(
                "error_invalid_variable_name"
                if variable
                else "error_invalid_name",
                error=True,
            )
            return

        raw = self.path_edit.text().strip()
        path = Path(raw).expanduser() if raw else None
        if path is None or not path.is_dir():
            self._set_status("error_no_dir", error=True)
            return
        self._update_hint()
        self.base_dir = str(path.resolve())
        self.path_edit.setText(self.base_dir)
        self.settings.setValue("code_path", self.base_dir)
        self._set_path_status("path_saved", path=self.base_dir)

        # 已有搜索时先取消，但不阻塞窗口；旧任务的结果会被代次过滤
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
        self._stats_timer.stop()
        self._results_timer.stop()
        self._pending_results.clear()
        self._stop_requested = False
        self.search_generation += 1
        generation = self.search_generation
        self.search_base_dir = self.base_dir

        self.current_name = name
        self.current_mode = self.search_mode
        self.result_count = 0
        self._last_scanned = 0
        self.has_searched = True
        self.empty_state = "no_results"
        self._clear_results()
        self._update_count_label()
        self.results_view.set_empty_visible(False)
        self._set_searching(True)
        self._set_status(
            "searching", name=name, engine=SearchEngine.backend_name()
        )

        worker = SearchWorker(
            self.base_dir, name, self, self.current_mode
        )
        self.worker = worker
        self.workers.add(worker)
        worker.results_ready.connect(
            lambda results, gen=generation: self._on_results(results, gen)
        )
        worker.progress.connect(
            lambda scanned, gen=generation: self._on_progress(scanned, gen)
        )
        worker.finished_ok.connect(
            lambda stopped, scanned, elapsed, gen=generation:
            self._on_finished(stopped, scanned, elapsed, gen)
        )
        worker.failed.connect(
            lambda message, gen=generation: self._on_failed(message, gen)
        )
        worker.finished.connect(self._on_worker_done)
        worker.start()

    def stop_search(self):
        if self.worker is None or not self.worker.isRunning():
            return
        self._stop_requested = True
        self._stats_timer.stop()
        self._results_timer.stop()
        self.stop_btn.setEnabled(False)
        self._set_status("stopping", name=self.current_name)
        self.worker.stop()

    def _clear_results(self):
        self._results_timer.stop()
        self._pending_results.clear()
        self.results_view.clear()
        self.result_count = 0
        self.copy_all_btn.setEnabled(False)

    def copy_all_results(self):
        results = list(self.results_view.results) + self._pending_results
        if not results:
            return
        separator = "\n\n" + "-" * 60 + "\n\n"
        text = separator.join(
            result_to_text(result).rstrip() for result in results
        )
        QApplication.clipboard().setText(text)
        self._set_status("copied_all", count=len(results))

    def _on_results(self, results, generation):
        if generation != self.search_generation:
            return
        self._pending_results.extend(results)
        self.result_count = (
            len(self.results_view.results) + len(self._pending_results)
        )
        self.copy_all_btn.setEnabled(self.result_count > 0)
        if not self._results_timer.isActive():
            self._results_timer.start()
        self._schedule_stats()

    def _flush_pending_results(self):
        if not self._pending_results:
            return
        batch = self._pending_results
        self._pending_results = []
        self.results_view.append_results(batch, self.search_base_dir)

    def _on_progress(self, scanned, generation):
        if generation != self.search_generation:
            return
        self._last_scanned = scanned
        self._schedule_stats()

    def _on_finished(self, stopped, scanned, elapsed, generation):
        if generation != self.search_generation:
            return
        self._stats_timer.stop()
        self._results_timer.stop()
        self._stop_requested = False
        self._last_scanned = scanned
        self._flush_pending_results()
        self.result_count = len(self.results_view.results)
        self.copy_all_btn.setEnabled(self.result_count > 0)
        self.results_view.flush_layout()
        self._set_searching(False)
        key = "stopped_summary" if stopped else "finished_summary"
        self._set_status(
            key,
            scanned=scanned,
            count=self.result_count,
            seconds=elapsed,
            **self._status_kind_kwargs(),
        )
        self._update_count_label()
        if self.result_count == 0:
            self.empty_state = "no_results"
            self._update_empty_label()
            self.results_view.set_empty_visible(True)
        else:
            self.results_view.set_empty_visible(False)

    def _on_failed(self, message, generation):
        if generation != self.search_generation:
            return
        self._stats_timer.stop()
        self._results_timer.stop()
        self._stop_requested = False
        self._flush_pending_results()
        self.result_count = len(self.results_view.results)
        self.copy_all_btn.setEnabled(self.result_count > 0)
        self.results_view.flush_layout()
        self._set_searching(False)
        self._set_status("search_failed", message=message, error=True)
        if self.result_count == 0:
            self.empty_state = "failed"
            self._update_empty_label()
            self.results_view.set_empty_visible(True)
        else:
            self.results_view.set_empty_visible(False)

    def _on_worker_done(self):
        worker = self.sender()
        self.workers.discard(worker)
        if worker is self.worker:
            self.worker = None
        if worker is not None:
            worker.deleteLater()

    # -- 窗口关闭 -----------------------------------------------------------

    def closeEvent(self, event):
        for worker in list(self.workers):
            worker.stop()
        for worker in list(self.workers):
            worker.wait(3000)
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


def _configure_qt_platform():
    """Wayland 会话下 Qt 的输入法集成存在崩溃风险，优先使用 XWayland。"""
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if not (os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY")):
        return
    display = os.environ["DISPLAY"]
    if display.startswith(":"):
        number = display[1:].split(".")[0]
        if number.isdigit() and not os.path.exists(f"/tmp/.X11-unix/X{number}"):
            return
    os.environ["QT_QPA_PLATFORM"] = "xcb"


def _set_windows_app_user_model_id():
    """让 Windows 任务栏把本程序当作独立应用分组/固定。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p("FuncFinder.FuncFinder")
        )
    except Exception:
        pass


def main():
    if len(sys.argv) == 2 and sys.argv[1] in ("-V", "--version"):
        print(f"{APP_NAME} {__version__}")
        return 0

    desktop_exit = _handle_desktop_cli()
    if desktop_exit is not None:
        return desktop_exit

    _set_windows_app_user_model_id()
    _configure_qt_platform()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    _ensure_desktop_integration()
    icon = _load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
