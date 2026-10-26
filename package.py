#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FuncFinder 自动打包脚本（PyInstaller）

功能：
    - 自动创建独立构建虚拟环境，安装 PySide6-Essentials 和 PyInstaller；
    - 自动下载 ripgrep（rg），并把它内置到最终程序中；
    - 自动使用 assets/ 下的 FF 图标（窗口/桌面/Windows exe）；
    - Linux 下内置桌面集成：首次运行自动安装 func-finder.desktop 和
      hicolor 图标，Ubuntu Dock 才能显示 FF 图标（PyInstaller 的 --icon
      在 Linux 上不会把图标写进 ELF）；
    - 支持 onedir（默认）和 onefile，可选生成 zip / tar.gz 分发包；
    - 可选生成 GitHub Actions 工作流，在 Windows/Linux 原生 runner 上打包。

重要限制：
    PyInstaller 不支持跨操作系统编译。Windows 程序必须在 Windows 上构建，
    Linux 程序必须在 Linux 上构建。需要同时生成两个平台的包时，请使用
    --write-ci 生成 GitHub Actions 工作流，或在目标系统上运行本脚本。

用法：
    python3 package.py --target linux
    python3 package.py --target windows
    python3 package.py --target linux --onefile --archive
    python3 package.py --target linux --no-rg
    python3 package.py --write-ci
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


def _configure_utf8_stdio():
    """Windows runner 的默认终端编码可能无法输出中文日志。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


_configure_utf8_stdio()

APP_NAME = "FuncFinder"
APP_SCRIPT = "func_finder.py"
RG_VERSION = "15.2.0"
RG_BASE_URL = "https://github.com/BurntSushi/ripgrep/releases/download"

RUNTIME_REQUIREMENTS = ["PySide6-Essentials>=6.8,<7"]
BUILD_REQUIREMENTS = ["pyinstaller>=6.11"]

ROOT = Path(__file__).resolve().parent
PACKAGING_DIR = ROOT / ".packaging"
CACHE_DIR = PACKAGING_DIR / "cache"
RUNTIME_HOOK = PACKAGING_DIR / "runtime_hook.py"
ICON_PNG = ROOT / "assets" / "func_finder.png"
ICON_WINDOW_PNG = ROOT / "assets" / "func_finder_window.png"
ICON_ICO = ROOT / "assets" / "func_finder.ico"

# (target, arch) -> (ripgrep target triple, archive extension, executable name)
RG_ASSETS = {
    ("linux", "x86_64"): ("x86_64-unknown-linux-musl", "tar.gz", "rg"),
    ("linux", "aarch64"): ("aarch64-unknown-linux-musl", "tar.gz", "rg"),
    ("windows", "x86_64"): ("x86_64-pc-windows-msvc", "zip", "rg.exe"),
    ("windows", "aarch64"): ("aarch64-pc-windows-msvc", "zip", "rg.exe"),
}


class PackagingError(RuntimeError):
    """打包流程中可预期的错误。"""


@dataclass
class BuildPaths:
    target: str
    arch: str
    dist_root: Path
    artifact: Path


def log(message):
    print(message, flush=True)


def resolve_python_argument(value):
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve()
    found = shutil.which(value)
    if found:
        return Path(found).resolve()
    raise PackagingError(f"找不到 Python 解释器：{value}")


def normalize_arch():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    return machine or "unknown"


def host_target():
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return None


def run_command(command, **kwargs):
    log("$ " + " ".join(str(part) for part in command))
    return subprocess.run(command, check=True, **kwargs)


def human_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def check_dependencies(python):
    code = (
        "import PySide6, PyInstaller; "
        "print('PySide6', PySide6.__version__, "
        "      '| PyInstaller', PyInstaller.__version__)"
    )
    result = subprocess.run(
        [str(python), "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        log("  " + result.stdout.strip())
        return True
    return False


def ensure_build_python(args):
    if args.no_venv:
        # 不要 resolve()：venv 的 python 通常是指向系统解释器的符号链接，
        # resolve 后会丢失 venv 里的 PySide6/PyInstaller。
        python = Path(sys.executable)
        if args.python:
            raise PackagingError("--python 不能和 --no-venv 同时使用。")
    else:
        base_python = (
            resolve_python_argument(args.python)
            if args.python
            else Path(sys.executable)
        )

        base_python = base_python.resolve()
        venv_dir = PACKAGING_DIR / "venv"
        marker = PACKAGING_DIR / "venv-python.txt"
        if host_target() == "windows":
            python = venv_dir / "Scripts" / "python.exe"
        else:
            python = venv_dir / "bin" / "python"

        if python.is_file():
            recorded = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
            if recorded and recorded != str(base_python):
                log(f"构建环境使用的 Python 已变化，重建：{venv_dir}")
                shutil.rmtree(venv_dir, ignore_errors=True)
            elif not recorded and args.python:
                log(f"旧构建环境解释器未知，重建：{venv_dir}")
                shutil.rmtree(venv_dir, ignore_errors=True)

        if not python.is_file():
            log(f"创建构建虚拟环境：{venv_dir}")
            run_command([str(base_python), "-m", "venv", str(venv_dir)])
            marker.write_text(str(base_python), encoding="utf-8")

    if args.skip_deps:
        log("检查构建依赖 ...")
        if not check_dependencies(python):
            raise PackagingError(
                "构建依赖不完整。请去掉 --skip-deps，或手动安装 "
                + " ".join(RUNTIME_REQUIREMENTS + BUILD_REQUIREMENTS)
            )
        return python

    if check_dependencies(python):
        return python

    log("安装构建依赖（PySide6-Essentials、PyInstaller）...")
    try:
        run_command(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                *RUNTIME_REQUIREMENTS,
                *BUILD_REQUIREMENTS,
            ]
        )
    except subprocess.CalledProcessError as exc:
        raise PackagingError(
            "依赖安装失败。若使用 --no-venv 的系统 Python，请改用默认虚拟环境，"
            "或先手动安装依赖。"
        ) from exc

    if not check_dependencies(python):
        raise PackagingError("依赖安装完成但仍无法导入 PySide6/PyInstaller。")
    return python


def _download(url, destination):
    log(f"下载：{url}")
    request = urllib.request.Request(url, headers={"User-Agent": "FuncFinder-packager"})
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            with temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        temporary.replace(destination)
    except (OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        raise PackagingError(f"下载失败：{url}\n{exc}") from exc


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_sha256(text):
    """从校验文件中提取 SHA256。

    Linux 发布资产只包含裸哈希，而 Windows 资产是 CertUtil 的输出：
        SHA256 hash of ripgrep-...zip:
        71b2...
        CertUtil: -hashfile command completed successfully.
    因此不能简单取 text.split()[0]。
    """
    match = re.search(r"\b[0-9a-fA-F]{64}\b", text)
    return match.group(0).lower() if match else ""


def _verify_checksum(archive, checksum_url):
    try:
        request = urllib.request.Request(
            checksum_url, headers={"User-Agent": "FuncFinder-packager"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        log(f"  ! 无法获取校验文件，跳过 SHA256 校验：{exc}")
        return

    expected = _parse_sha256(text)
    if not expected:
        log("  ! 校验文件为空或格式无法识别，跳过 SHA256 校验。")
        return

    actual = _sha256(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise PackagingError(
            f"ripgrep 压缩包 SHA256 校验失败：\n  期望 {expected}\n  实际 {actual}"
        )
    log("  SHA256 校验通过。")


def _extract_binary(archive, extension, executable_name, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if extension == "zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if Path(member.filename).name == executable_name:
                    destination.write_bytes(bundle.read(member))
                    break
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if member.isfile() and Path(member.name).name == executable_name:
                    extracted = bundle.extractfile(member)
                    if extracted is None:
                        continue
                    destination.write_bytes(extracted.read())
                    break

    if not destination.is_file():
        raise PackagingError(
            f"ripgrep 压缩包中未找到 {executable_name}：{archive}"
        )
    destination.chmod(0o755)


def _copy_local_rg(source, destination):
    source = Path(source).expanduser()
    if not source.is_file():
        found = shutil.which(str(source))
        if found:
            source = Path(found)
    source = source.resolve()
    if not source.is_file():
        raise PackagingError(f"指定的 ripgrep 不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o755)
    log(f"使用本地 ripgrep：{source}")
    return destination


def prepare_ripgrep(args, target, arch):
    if args.no_rg:
        log("按参数要求不内置 ripgrep。")
        return None

    executable_name = "rg.exe" if target == "windows" else "rg"

    if args.rg:
        destination = CACHE_DIR / f"local-{target}-{arch}" / executable_name
        return _copy_local_rg(args.rg, destination)

    asset_info = RG_ASSETS.get((target, arch))
    if asset_info is None:
        log(f"  ! 暂不支持为 {target}/{arch} 自动下载 ripgrep，跳过内置。")
        return None

    triple, extension, executable_name = asset_info
    asset_name = f"ripgrep-{args.rg_version}-{triple}.{extension}"
    archive = CACHE_DIR / f"ripgrep-{args.rg_version}-{triple}" / asset_name
    destination = archive.parent / executable_name

    if destination.is_file():
        log(f"使用缓存 ripgrep：{destination}")
        return destination

    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        url = f"{RG_BASE_URL}/{args.rg_version}/{asset_name}"
        try:
            _download(url, archive)
        except PackagingError as exc:
            system_rg = shutil.which("rg")
            if system_rg:
                log(f"  ! {exc}")
                log(f"  ! 改用系统 ripgrep：{system_rg}")
                return _copy_local_rg(Path(system_rg), destination)
            log(f"  ! {exc}")
            log("  ! 未内置 ripgrep；打包后的程序会回退到 grep/纯 Python 搜索。")
            return None

    _verify_checksum(archive, f"{RG_BASE_URL}/{args.rg_version}/{asset_name}.sha256")
    try:
        _extract_binary(archive, extension, executable_name, destination)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        archive.unlink(missing_ok=True)
        raise PackagingError(f"解压 ripgrep 失败：{archive}\n{exc}") from exc
    log(f"ripgrep 已就绪：{destination}")
    return destination


def write_runtime_hook():
    """生成 PyInstaller 运行时钩子，让程序能找到随包内置的 rg。"""
    RUNTIME_HOOK.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_HOOK.write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "_bundle_dir = getattr(sys, '_MEIPASS', '')\n"
        "_exe_dir = os.path.dirname(os.path.abspath(sys.executable))\n"
        "_paths = []\n"
        "for _path in (_bundle_dir, _exe_dir):\n"
        "    if _path and os.path.isdir(_path) and _path not in _paths:\n"
        "        _paths.append(_path)\n"
        "_existing = os.environ.get('PATH', '')\n"
        "if _paths:\n"
        "    os.environ['PATH'] = os.pathsep.join(\n"
        "        _paths + ([_existing] if _existing else [])\n"
        "    )\n",
        encoding="utf-8",
    )
    return RUNTIME_HOOK


def build_application(python, args, target, arch, ripgrep):
    if not (ROOT / APP_SCRIPT).is_file():
        raise PackagingError(f"找不到主程序：{ROOT / APP_SCRIPT}")

    dist_root = ROOT / "dist" / target
    work_dir = ROOT / "build" / target
    if args.clean:
        shutil.rmtree(dist_root, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)
    dist_root.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 允许在 onedir/onefile 之间切换而不残留同名产物。
    for stale in (
        dist_root / args.name,
        dist_root / (args.name + ".exe"),
    ):
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
        elif stale.is_file():
            stale.unlink()

    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        args.name,
        "--distpath",
        str(dist_root),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(work_dir),
        "--console" if args.console else "--windowed",
    ]
    if args.onefile:
        command.append("--onefile")
    if args.clean:
        command.append("--clean")

    icon = None
    if args.icon:
        icon = args.icon.expanduser().resolve()
        if not icon.is_file():
            raise PackagingError(f"图标文件不存在：{icon}")
    elif target == "windows" and ICON_ICO.is_file():
        icon = ICON_ICO

    if target == "windows":
        if icon is not None:
            command += ["--icon", str(icon)]
        else:
            log("  ! 未找到 assets/func_finder.ico，Windows exe 将使用默认图标。")
    elif args.icon is not None:
        # PyInstaller 在 Linux 上不支持给 ELF 内嵌图标资源；Dock 图标由
        # .desktop 文件和图标主题提供，因此这里明确忽略 --icon。
        log("  ! Linux 可执行文件无法内嵌图标，已忽略 --icon。")
        log("    请替换 assets/func_finder.png / func_finder_window.png。")

    if target == "linux" and not ICON_PNG.is_file():
        raise PackagingError(
            "缺少 Linux 桌面图标 assets/func_finder.png，无法注册 Dock 图标。"
        )

    # 窗口/任务栏图标由 Qt 从随包资源中读取，与可执行文件图标相互独立。
    # 同时带上 192px（X11 _NET_WM_ICON）和 512px（桌面启动器）两个版本。
    for icon_file in (ICON_WINDOW_PNG, ICON_PNG):
        if icon_file.is_file():
            command += ["--add-data", f"{icon_file}{os.pathsep}assets"]
    if not ICON_WINDOW_PNG.is_file() and not ICON_PNG.is_file():
        log("  ! 未找到窗口图标资源，打包后的窗口将没有图标。")

    if ripgrep is not None:
        hook = write_runtime_hook()
        command += [
            "--add-binary",
            f"{ripgrep}{os.pathsep}.",
            "--runtime-hook",
            str(hook),
        ]
    command.append(str(ROOT / APP_SCRIPT))

    log(f"开始打包 {target}/{arch} ...")
    try:
        run_command(command, cwd=ROOT)
    except subprocess.CalledProcessError as exc:
        raise PackagingError(
            f"PyInstaller 打包失败（退出码 {exc.returncode}）。"
            f"构建日志目录：{work_dir}"
        ) from exc

    if args.onefile:
        artifact = dist_root / (args.name + (".exe" if target == "windows" else ""))
    else:
        artifact = dist_root / args.name
    if not artifact.exists():
        raise PackagingError(f"打包完成但未找到产物：{artifact}")

    if ripgrep is not None and not args.onefile:
        executable_name = "rg.exe" if target == "windows" else "rg"
        candidates = [
            artifact / executable_name,
            artifact / "_internal" / executable_name,
        ]
        if not any(candidate.is_file() for candidate in candidates):
            raise PackagingError(
                "打包产物中未找到内置 ripgrep，运行时将无法使用 rg。"
            )

    if not args.onefile:
        for icon_file in (ICON_WINDOW_PNG, ICON_PNG):
            if not icon_file.is_file():
                continue
            icon_candidates = [
                artifact / "assets" / icon_file.name,
                artifact / "_internal" / "assets" / icon_file.name,
            ]
            if not any(candidate.is_file() for candidate in icon_candidates):
                raise PackagingError(
                    f"打包产物中未找到图标资源 assets/{icon_file.name}。"
                )

    if not args.onefile:
        exe_name = args.name + (".exe" if target == "windows" else "")
        readme = artifact / "README.txt"
        readme_text = (
            "FuncFinder\n"
            "==========\n\n"
            f"启动：{exe_name if target == 'windows' else './' + exe_name}\n"
            "必须保留整个目录，尤其是 _internal/；不要只复制可执行文件。\n"
            "Keep this whole directory, especially _internal/.\n"
            "Do not copy the executable file alone.\n"
        )
        if target == "linux":
            readme_text += (
                "\nUbuntu Dock 图标：首次启动会自动注册桌面入口；\n"
                "如未生效，运行 ./"
                f"{exe_name} --install-desktop\n"
                "卸载桌面入口：./"
                f"{exe_name} --uninstall-desktop\n"
            )
        readme.write_text(readme_text, encoding="utf-8")

    if not args.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    return BuildPaths(target=target, arch=arch, dist_root=dist_root,
                      artifact=artifact)


def create_archive(paths, name):
    extension = "zip" if paths.target == "windows" else "gztar"
    base = paths.dist_root / f"{name}-{paths.target}-{paths.arch}"
    archive = shutil.make_archive(
        str(base), extension, root_dir=str(paths.artifact.parent),
        base_dir=paths.artifact.name
    )
    archive_path = Path(archive)
    log(f"分发包：{archive_path} ({human_size(archive_path.stat().st_size)})")
    return archive_path


CI_TEMPLATE = """name: Package FuncFinder

on:
  workflow_dispatch:
  push:
    tags:
      - "v*"

permissions:
  contents: read

jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-latest
            target: linux
          - os: windows-latest
            target: windows
    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Linux Qt runtime libraries
        if: runner.os == 'Linux'
        run: sudo apt-get update && sudo apt-get install -y libegl1 libgl1

      - name: Build package
        run: python package.py --target ${{ matrix.target }} --no-venv --archive

      - uses: actions/upload-artifact@v4
        with:
          name: FuncFinder-${{ matrix.target }}
          path: dist/**
          if-no-files-found: error
"""


def write_ci_workflow(ci_path):
    path = ci_path
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CI_TEMPLATE, encoding="utf-8")
    log(f"已生成 GitHub Actions 工作流：{path}")


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="使用 PyInstaller 打包 FuncFinder（自动安装依赖并内置 ripgrep）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 package.py --target linux\n"
            "  python3 package.py --target linux --onefile --archive\n"
            "  python3 package.py --target windows\n"
            "  python3 package.py --write-ci\n"
        ),
    )
    parser.add_argument(
        "--target", choices=("current", "linux", "windows"), default="current",
        help="目标平台；默认当前系统（PyInstaller 不支持跨系统编译）",
    )
    parser.add_argument(
        "--onefile", action="store_true",
        help="打包为单个可执行文件（默认 onedir，启动更快）",
    )
    parser.add_argument(
        "--console", action="store_true",
        help="保留控制台窗口，便于查看报错（默认无控制台）",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="清理旧的构建目录和 PyInstaller 缓存后再打包",
    )
    parser.add_argument(
        "--no-venv", action="store_true",
        help="不创建 .packaging/venv，直接使用当前 Python",
    )
    parser.add_argument(
        "--skip-deps", action="store_true",
        help="跳过依赖安装，直接检查并使用当前环境",
    )
    parser.add_argument(
        "--python", metavar="PYTHON",
        help="创建构建虚拟环境时使用的 Python 路径或命令名",
    )
    parser.add_argument(
        "--no-rg", action="store_true",
        help="不下载、不内置 ripgrep（程序将回退到 grep/Python 搜索）",
    )
    parser.add_argument(
        "--rg", type=Path, metavar="PATH",
        help="使用本地 ripgrep 可执行文件，而不是从 GitHub 下载",
    )
    parser.add_argument(
        "--rg-version", metavar="VERSION", default=RG_VERSION,
        help=f"自动下载的 ripgrep 版本（默认 {RG_VERSION}）",
    )
    parser.add_argument(
        "--name", default=APP_NAME,
        help=f"可执行文件名称（默认 {APP_NAME}）",
    )
    parser.add_argument(
        "--icon", type=Path,
        help="Windows exe 图标（通常 .ico）；Linux 会忽略并改用 assets/*.png",
    )
    parser.add_argument(
        "--archive", action="store_true",
        help="额外生成 zip（Windows）或 tar.gz（Linux）分发包",
    )
    parser.add_argument(
        "--keep-work", action="store_true",
        help="保留 PyInstaller 的 build 工作目录，便于排查问题",
    )
    parser.add_argument(
        "--write-ci", action="store_true",
        help="生成 GitHub Actions 工作流（同时构建 Windows 和 Linux）后退出",
    )
    parser.add_argument(
        "--ci-path", type=Path, default=Path(".github/workflows/package.yml"),
        help="--write-ci 的输出路径（默认 .github/workflows/package.yml）",
    )
    args = parser.parse_args(argv)

    if args.no_rg and args.rg:
        parser.error("--no-rg 和 --rg 不能同时使用。")
    if args.python and args.no_venv:
        parser.error("--python 和 --no-venv 不能同时使用。")
    if Path(args.name).name != args.name or not args.name:
        parser.error("--name 只能是文件名，不能包含路径分隔符。")
    return args


def main(argv=None):
    args = parse_arguments(argv)

    if args.write_ci:
        try:
            write_ci_workflow(args.ci_path)
        except OSError as exc:
            log(f"错误：无法写入工作流：{exc}")
            return 1
        log("提示：把工作流和 package.py 提交到 GitHub 后，可在 Actions 中手动触发，"
            "或在推送 v* 标签时自动构建 Windows/Linux 两个包。")
        return 0

    host = host_target()
    if host is None:
        log("错误：当前仅支持在 Windows 或 Linux 上打包。")
        log("可运行 --write-ci 生成 GitHub Actions 工作流。")
        return 2

    target = host if args.target == "current" else args.target
    arch = normalize_arch()
    if target != host:
        log(f"错误：PyInstaller 不能跨系统编译。当前主机是 {host}，"
            f"无法直接生成 {target} 程序。")
        log("")
        log("可选方案：")
        log(f"  1. 在 {target} 系统上运行：python package.py --target {target}")
        log("  2. 生成 GitHub Actions 工作流，在两个原生 runner 上自动打包：")
        log("       python package.py --write-ci")
        return 2

    log(f"目标平台：{target} / {arch}")
    log(f"Python：{sys.version.split()[0]}")

    try:
        python = ensure_build_python(args)
        ripgrep = prepare_ripgrep(args, target, arch)
        paths = build_application(python, args, target, arch, ripgrep)
        archive = create_archive(paths, args.name) if args.archive else None
    except PackagingError as exc:
        log(f"\n错误：{exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        log(f"\n错误：命令执行失败（退出码 {exc.returncode}）。")
        return 1
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        log(f"\n错误：{exc}")
        return 1
    except KeyboardInterrupt:
        log("\n已取消。")
        return 130

    log("")
    log("打包完成：")
    if args.onefile:
        launch = paths.artifact
        log(f"  单文件程序：{paths.artifact}")
    else:
        launch = paths.artifact / (args.name + (".exe" if target == "windows" else ""))
        log(f"  程序目录：{paths.artifact}")
        log(f"  启动方式：{launch}")
        log("  注意：onedir 模式必须分发整个目录（含 _internal/），")
        log("        不能只复制可执行文件；可用 --archive 或 --onefile。")
    log(f"  ripgrep：{'已内置' if ripgrep is not None else '未内置（使用系统 grep/Python 回退）'}")
    if target == "linux":
        quoted_launch = shlex.quote(str(launch))
        log("  Ubuntu Dock 图标：已内置，首次启动会自动注册")
        log("    桌面入口：~/.local/share/applications/func-finder.desktop")
        log(f"    立即注册/修复：{quoted_launch} --install-desktop")
        log(f"    卸载桌面入口：{quoted_launch} --uninstall-desktop")
    if archive:
        log(f"  分发包：{archive}")
    log(f"  平台：{target} / {arch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
