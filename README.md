# FuncFinder

**在任意代码目录中快速定位函数定义和完整实现。**
**Find function definitions and full implementations in any codebase.**

[![CI](https://github.com/zhijian-yan/FuncFinder/actions/workflows/ci.yml/badge.svg)](https://github.com/zhijian-yan/FuncFinder/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/zhijian-yan/FuncFinder?display_name=tag&sort=semver)](https://github.com/zhijian-yan/FuncFinder/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

![FuncFinder 主界面](docs/screenshot.png)

## 简介

FuncFinder 是一个基于 Python + PySide6/Qt 6 的桌面工具。选择一个代码目录，
输入函数名，它会搜索函数定义并把签名、文件位置、行号和完整实现展示成卡片，
适合在大型项目中快速阅读不熟悉的代码。

它不是语言服务器，也不依赖编译器索引；它通过扩展名识别语言，并用启发式规则
提取函数边界。因此对常见代码足够快，也允许在混合语言目录中直接使用。

## 功能

- **多语言**：C/C++、Python、Go、Rust、Java、C#、JavaScript/TypeScript、
  Kotlin、Scala、Swift、Dart、Objective-C（C 函数部分）。
- **快速搜索**：优先使用 `ripgrep`，其次系统 `grep`，最后纯 Python 扫描。
- **后台搜索**：搜索在工作线程执行，界面不卡顿，可随时停止。
- **完整实现**：不仅显示定义行，还会根据花括号/缩进提取整个函数体。
- **语法高亮**：按语言高亮关键字、字符串、注释和数字。
- **结果操作**：单条复制、复制全部、显示文件位置和行号。
- **桌面体验**：浅色/深色主题、中英文切换、窗口/任务栏图标。
- **Linux 集成**：首次运行自动注册 `.desktop` 和 hicolor 图标，Ubuntu Dock
  能正确显示图标；也可手动执行 `--install-desktop`。
- **跨平台打包**：`package.py` 支持 PyInstaller 的 onedir/onefile，
  Linux 和 Windows 均有 GitHub Actions 构建。

## 支持语言

| 语言 | 扩展名示例 |
| --- | --- |
| C / C++ | `.c` `.h` `.cc` `.cpp` `.hpp` `.ino` |
| Objective-C | `.m` `.mm` |
| Python | `.py` `.pyi` |
| Go | `.go` |
| Rust | `.rs` |
| Java | `.java` |
| C# | `.cs` |
| JavaScript / TypeScript | `.js` `.jsx` `.ts` `.tsx` |
| Kotlin | `.kt` `.kts` |
| Scala | `.scala` |
| Swift | `.swift` |
| Dart | `.dart` |

## 快速开始

要求：Python 3.10+。源码运行支持 Linux/Windows/macOS；打包脚本目前支持
Linux/Windows。

```bash
git clone https://github.com/zhijian-yan/FuncFinder.git
cd FuncFinder
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python func_finder.py
```

### 安装 ripgrep（推荐）

`ripgrep` 可以显著加快大目录的搜索速度：

```bash
# Ubuntu / Debian
sudo apt install ripgrep

# macOS (Homebrew)
brew install ripgrep

# Windows (winget)
winget install BurntSushi.ripgrep.MSVC
```

没有 `rg` 时会自动回退到 `grep`，再回退到纯 Python 扫描。可以用环境变量强制
指定后端：

```bash
FUNC_FINDER_BACKEND=rg python func_finder.py
FUNC_FINDER_BACKEND=grep python func_finder.py
FUNC_FINDER_BACKEND=python python func_finder.py
```

## 使用

1. 启动程序，点击「选择目录」指定代码目录。
2. 输入函数名（例如 `i2c_transfer`、`parse_config`、`main`）。
3. 点击「搜索」，结果会以卡片形式显示。
4. 使用卡片上的复制按钮复制完整实现，或点击「复制全部」。
5. 右上角可以切换主题和语言；配置会自动保存。

支持的函数名包括字母、数字、下划线、`$`，且不能以数字开头。

## 命令行

```bash
python func_finder.py --version
python func_finder.py --install-desktop     # Linux：注册桌面入口和图标
python func_finder.py --uninstall-desktop   # Linux：删除桌面入口和图标
```

Linux 桌面入口写入当前用户目录，不需要 `sudo`：

```text
~/.local/share/applications/func-finder.desktop
~/.local/share/icons/hicolor/512x512/apps/func-finder.png
~/.local/share/icons/hicolor/192x192/apps/func-finder.png
```

如果不想在启动时自动注册，可以设置：

```bash
FUNC_FINDER_NO_DESKTOP_ENTRY=1 ./FuncFinder
```

配置位置：Linux 下为 `~/.config/FuncFinder/FuncFinder.conf`，Windows 下由
Qt `QSettings` 保存到注册表。

## 打包

打包使用 [PyInstaller](https://pyinstaller.org/)。`package.py` 会自动创建
构建虚拟环境、安装依赖，并把 `assets/` 和 ripgrep 一并放进产物。

```bash
# Linux：单文件 + tar.gz 分发包
python3 package.py --target linux --onefile --archive

# Windows：单文件 + zip 分发包
python package.py --target windows --onefile --archive
```

不加 `--onefile` 时默认生成 onedir 目录（启动更快，但必须分发整个目录）。
PyInstaller 不支持跨系统编译：Linux 包在 Linux 上构建，Windows 包在 Windows
上构建。推送到 `v*` 标签时，仓库的 Release 工作流会同时在两个原生 runner 上
构建并上传发布资产。

### Linux Dock 图标说明

PyInstaller 的 `--icon` 在 Linux 上不会把图标写进 ELF。Dock 图标依赖窗口的
`app_id`/`WM_CLASS` 与 `.desktop` 文件匹配，因此程序首次运行会安装桌面入口；
如果图标没有立即刷新，重新启动一次程序或注销后重新登录。

## 项目结构

```text
.
├── func_finder.py              # 主程序（含 Linux 桌面集成）
├── package.py                  # PyInstaller 打包脚本
├── assets/                     # 图标资源
├── docs/screenshot.png         # README 截图
├── tests/                      # 不依赖显示器的单元测试
├── requirements.txt            # 运行依赖
├── requirements-dev.txt        # 开发/打包依赖
├── CHANGELOG.md
└── .github/workflows/          # CI 与 Release
```

## 开发

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python -m py_compile func_finder.py package.py
python -m unittest discover -s tests -v
python func_finder.py --version
```

CI 会在 Ubuntu 上运行同样的检查；Release 工作流会为 `v*` 标签构建
Linux/Windows 单文件包并创建 GitHub Release。

## 已知限制

- 函数提取是启发式的，宏、复杂模板、重载和嵌套定义可能需要人工确认。
- 超大目录建议安装 `ripgrep`，否则首次搜索会明显变慢。
- 本项目目前只提供 Linux/Windows 打包脚本；macOS 打包需要自行调整。
- 桌面图标集成只针对 Linux/GNOME；其他桌面环境通常也能读取 `.desktop`。

## 许可证

[MIT](LICENSE) © 2026 Zhijian
