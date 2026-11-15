#!/usr/bin/env python3
"""KarenBot 安装引导器（CLI 核心）。

设计约束：
- 安装器本身不内置应用包；只负责选择来源、测速、下载、校验、解压、建 venv、
  配置主题/字体、创建快捷方式。
- 支持 ``--source-dir`` / ``--archive`` 做离线/本地测试，不依赖 GitHub。
- 支持在线安装：Linux 下载 ``KarenBot-backend`` 和 GUI/WebUI 源码包；
  Windows 原生 GUI 下载安装器 Release 提供的 GTK4 便携预构建包。
- 安装器不构建 deb 等系统包；Linux 以 install.sh + CLI 为主。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import unquote
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

APP_NAME = "KarenBot"
IS_WINDOWS = os.name == "nt"
if IS_WINDOWS:
    _LOCALAPPDATA = Path(
        os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    )
    DEFAULT_INSTALL_DIR = _LOCALAPPDATA / "Programs" / "KarenBot"
    # 中文用户名会让 %LOCALAPPDATA% 带中文字符，venv/pip/cmd 都可能失败。
    if not str(DEFAULT_INSTALL_DIR).isascii():
        DEFAULT_INSTALL_DIR = Path(
            os.environ.get("PUBLIC") or r"C:\Users\Public"
        ) / "KarenBot"
    if not str(DEFAULT_INSTALL_DIR).isascii():
        DEFAULT_INSTALL_DIR = Path(r"C:\KarenBot")
else:
    DEFAULT_INSTALL_DIR = Path.home() / ".local" / "opt" / "karenbot"


def _resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    bundled = base / name
    if bundled.is_file():
        return bundled
    return Path(__file__).with_name(name)


MIRRORS_FILE = _resource_path("mirrors.json")
WINDOWS_PACKAGE_FILE = _resource_path("windows-gui-package.json")
INSTALLER_VERSION = "0.1.0-dev"
USER_AGENT = f"KarenBot-Installer/{INSTALLER_VERSION}"
PROBE_BYTES = 512 * 1024
PROBE_TIMEOUT = 8.0
PROBE_WORKERS = 16
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_CHUNK = 256 * 1024

# 在线安装默认从 GitHub 查询：优先最新稳定版，没有稳定版时选择最新 Release。
# backend 提供核心程序 + 后端；GUI/WebUI 仍从各自仓库下载界面模块。
ONLINE_TAG = "auto"
ONLINE_BACKEND_REPO = "CN-BI5UNF/KarenBot-backend"
ONLINE_FRONTEND_REPOS = {
    "gui": "CN-BI5UNF/KarenBot-GUI",
    "webui": "CN-BI5UNF/KarenBot-WebUI",
}
ONLINE_ARCHIVE_TEMPLATE = "https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"
WINDOWS_BUNDLE_REPO = "CN-BI5UNF/KarenBot-Installer"
WINDOWS_GUI_ASSET_NAME = "KarenBot-Windows-x64.zip"
WINDOWS_GUI_ASSET_ENV = "KAREN_GUI_WINDOWS_ASSET"

GUI_SKINS = {
    "karen": "Karen 暖色（内置，最接近 Linux 目标机）",
    "system": "系统 Adwaita（GTK4 默认）",
    "none": "不使用美化包（使用 GUI 默认外观）",
}
RENDER_MODES = {
    "auto": "自动（失败时由 GUI 启动器回退）",
    "cairo": "软件渲染（Cairo；黑屏/崩溃时使用）",
    "gl": "硬件渲染（OpenGL）",
}

FONT_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "name": "NotoSansSC-VF.otf",
        "sha256": "d13ed01ec8aa45d6178999b648e96fb92150683e9f8e2a581f2acf208dcbe44b",
        "urls": (
            "https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
            "Sans/Variable/OTF/Subset/NotoSansSC-VF.otf",
            "https://github.com/notofonts/noto-cjk/raw/main/"
            "Sans/Variable/OTF/Subset/NotoSansSC-VF.otf",
            "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/"
            "Sans/Variable/OTF/Subset/NotoSansSC-VF.otf",
            "https://fastly.jsdelivr.net/gh/notofonts/noto-cjk@main/"
            "Sans/Variable/OTF/Subset/NotoSansSC-VF.otf",
            "https://raw.gitmirror.com/notofonts/noto-cjk/main/"
            "Sans/Variable/OTF/Subset/NotoSansSC-VF.otf",
        ),
    },
    {
        "name": "NotoSerifSC-VF.otf",
        "sha256": "71b4d3ded2d90ff43bb75a4e48cdbe170f0b8d5486dc89ff87f2a1728b56da64",
        "urls": (
            "https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
            "Serif/Variable/OTF/Subset/NotoSerifSC-VF.otf",
            "https://github.com/notofonts/noto-cjk/raw/main/"
            "Serif/Variable/OTF/Subset/NotoSerifSC-VF.otf",
            "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/"
            "Serif/Variable/OTF/Subset/NotoSerifSC-VF.otf",
            "https://fastly.jsdelivr.net/gh/notofonts/noto-cjk@main/"
            "Serif/Variable/OTF/Subset/NotoSerifSC-VF.otf",
            "https://raw.gitmirror.com/notofonts/noto-cjk/main/"
            "Serif/Variable/OTF/Subset/NotoSerifSC-VF.otf",
        ),
    },
)

# 用户全局 Python 缺失/不完整时，下载到安装目录的便携 Python。
# 来源：astral-sh/python-build-standalone 的 install_only 构建（含 pip/venv）。
PYTHON_RUNTIME_VERSION = "3.12.16"
PYTHON_RUNTIME_RELEASE = "20261110"
PYTHON_RUNTIME_DIRNAME = "python-runtime"
_PYTHON_RUNTIME_BASE = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PYTHON_RUNTIME_RELEASE}"
)
PYTHON_RUNTIME_SOURCES: dict[str, dict[str, str]] = {
    "windows-x64": {
        "label": "Windows x64",
        "archive": (
            f"cpython-{PYTHON_RUNTIME_VERSION}%2B{PYTHON_RUNTIME_RELEASE}"
            "-x86_64-pc-windows-msvc-install_only.tar.gz"
        ),
        "sha256": "e23fe5b16926de1d81661b1a97352e89fb167549c55a6825796ccd0f32ec7f1b",
    },
    "windows-arm64": {
        "label": "Windows ARM64",
        "archive": (
            f"cpython-{PYTHON_RUNTIME_VERSION}%2B{PYTHON_RUNTIME_RELEASE}"
            "-aarch64-pc-windows-msvc-install_only.tar.gz"
        ),
        "sha256": "7122bf62a0cf601527ba62e19ca715d64db86b269765da23937e9ac7f968f39c",
    },
    "linux-x64": {
        "label": "Linux x64",
        "archive": (
            f"cpython-{PYTHON_RUNTIME_VERSION}%2B{PYTHON_RUNTIME_RELEASE}"
            "-x86_64-unknown-linux-gnu-install_only.tar.gz"
        ),
        "sha256": "c2f87d4b4e8db3abddb0f116c6ccfc503d0cbdbb9ee8faacddad1b679ec12e99",
    },
    "linux-arm64": {
        "label": "Linux ARM64",
        "archive": (
            f"cpython-{PYTHON_RUNTIME_VERSION}%2B{PYTHON_RUNTIME_RELEASE}"
            "-aarch64-unknown-linux-gnu-install_only.tar.gz"
        ),
        "sha256": "4792f81750282597fb2a3389fe18b945ed2ba6aad5e45ad406d122c58cb33081",
    },
}
for _runtime_key, _runtime_value in PYTHON_RUNTIME_SOURCES.items():
    _runtime_value["url"] = f"{_PYTHON_RUNTIME_BASE}/{_runtime_value['archive']}"

# Windows 安装器内置的精简 Python（22 MB 左右），总成品仍 < 50 MiB。
# 安装时解压到 <安装目录>/python-runtime；解压失败才回退到网络下载。
BUNDLED_PYTHON_ARCHIVE_NAME = "python-runtime.tar.gz"
BUNDLED_PYTHON_HASH_NAME = "python-runtime.sha256"
BUNDLED_PYTHON_SOURCES: dict[str, dict[str, Any]] = {
    "windows-x64": {
        "label": "Windows x64 stripped",
        "url": (
            f"{_PYTHON_RUNTIME_BASE}/cpython-{PYTHON_RUNTIME_VERSION}"
            f"%2B{PYTHON_RUNTIME_RELEASE}"
            "-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
        ),
        "sha256": "355ee61b8207b91574491b1c6dc9b0ebb4408fc56bfafe498f3ef439314b52dc",
        "size": 22365213,
    },
}

DEFAULT_WINDOWS_PACKAGE: dict[str, Any] = {
    "schema": 1,
    "asset_name": WINDOWS_GUI_ASSET_NAME,
    "required_dirs": ["backend", "gui"],
    "python_candidates": [
        "runtime/python.exe",
        "runtime/bin/python.exe",
        "python.exe",
    ],
    "required_dlls": [
        "libgtk-4-1.dll",
        "libadwaita-1-0.dll",
        "libgobject-2.0-0.dll",
    ],
    "typelib_dirs": [
        "runtime/lib/girepository-1.0",
        "lib/girepository-1.0",
    ],
    "required_typelibs": ["Gtk-4.0.typelib", "Adw-1.typelib"],
    "launcher_hint": "runtime/python.exe -m gui",
}

_MIRROR_CACHE: list[dict[str, Any]] | None = None
_PACKAGE_CONTRACT_CACHE: dict[str, Any] | None = None
_MIRROR_OVERRIDE: str | None = None
_LOG_SINK: Callable[[str], None] | None = None
_PROGRESS_SINK: Callable[[int, int | None], None] | None = None
_STATUS_SINK: Callable[[str], None] | None = None


class InstallFailure(SystemExit):
    """安装失败；保留 ``SystemExit`` 兼容 CLI 和既有测试。"""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(code)
        self.message = message


class InstallCancelled(Exception):
    """用户在 GUI/CLI 中取消安装。"""


def set_log_sink(sink: Callable[[str], None] | None) -> None:
    """GUI 用：把安装日志转发到 UI；None 恢复 stdout。"""
    global _LOG_SINK
    _LOG_SINK = sink


def set_progress_sink(sink: Callable[[int, int | None], None] | None) -> None:
    """GUI 用：下载进度 (已下载字节, 总字节或 None)。"""
    global _PROGRESS_SINK
    _PROGRESS_SINK = sink


def set_status_sink(sink: Callable[[str], None] | None) -> None:
    """GUI 用：当前安装阶段（测速、下载、建 venv、安装主题等）。"""
    global _STATUS_SINK
    _STATUS_SINK = sink


def stage(message: str) -> None:
    """记录并上报当前阶段；GUI 用它更新进度弹窗。"""
    log(message)
    sink = _STATUS_SINK
    if sink is not None:
        try:
            sink(message)
        except Exception:  # noqa: BLE001
            pass


def reset_runtime_caches() -> None:
    """测试/切换镜像后清空进程内缓存。"""
    global _MIRROR_CACHE, _PACKAGE_CONTRACT_CACHE, _MIRROR_OVERRIDE
    _MIRROR_CACHE = None
    _PACKAGE_CONTRACT_CACHE = None
    _MIRROR_OVERRIDE = None


def log(message: str) -> None:
    line = f"[karen-install] {message}"
    sink = _LOG_SINK
    if sink is not None:
        try:
            sink(line)
        except Exception:  # noqa: BLE001 - UI 已退出时不能影响安装
            pass
        return
    stream = sys.stdout
    if stream is not None:
        try:
            print(line, file=stream, flush=True)
        except Exception:  # noqa: BLE001 - windowed 冻结版可能没有 stdout
            pass


def fail(message: str, code: int = 1) -> NoReturn:
    text = f"[karen-install] 错误：{message}"
    sink = _LOG_SINK
    if sink is not None:
        try:
            sink(text)
        except Exception:  # noqa: BLE001
            pass
    else:
        stream = sys.stderr
        if stream is not None:
            try:
                print(text, file=stream, flush=True)
            except Exception:  # noqa: BLE001
                pass
    raise InstallFailure(message, code)


def _check_cancel(cancel_event: Any | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InstallCancelled()


def _emit_progress(done: int, total: int | None) -> None:
    sink = _PROGRESS_SINK
    if sink is None:
        return
    try:
        sink(done, total)
    except Exception:  # noqa: BLE001
        pass


def _creation_flags() -> int:
    """GUI 冻结版在 Windows 上不要为 pip/venv 弹黑框。"""
    if IS_WINDOWS and getattr(sys, "frozen", False):
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def run(command: list[str], *, cwd: Path | None = None) -> None:
    log("执行：" + " ".join(command))
    subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        creationflags=_creation_flags(),
    )


def _ascii_install_suggestion() -> str:
    if IS_WINDOWS:
        public = Path(os.environ.get("PUBLIC") or r"C:\Users\Public")
        return str(public / "KarenBot")
    return str(DEFAULT_INSTALL_DIR)


def path_problem(path: Path, label: str = "路径") -> str | None:
    """Windows 下的中文/非 ASCII 路径是 venv、pip 和 cmd 的高发故障源。"""
    if not IS_WINDOWS:
        return None
    text = str(path)
    if text.isascii():
        return None
    return (
        f"{label}包含中文或其他非 ASCII 字符：{text}\n"
        "Windows 下这会导致 venv/pip/cmd 无法正常工作。\n"
        f"请改用只含英文、数字和空格的路径，例如：{_ascii_install_suggestion()}"
    )


def _validate_install_path(path: Path, label: str) -> None:
    problem = path_problem(path, label)
    if problem:
        fail(problem)


def ensure_writable_dir(path: Path, label: str = "安装目录") -> None:
    """提前检查权限，避免装到一半才在 venv 阶段报 PermissionError。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".karen-write-test-", dir=str(path)
        ):
            pass
    except OSError as exc:
        fail(
            f"{label}不可写：{path}\n"
            "请选择当前用户有写权限的目录。Windows 推荐：\n"
            f"  {_ascii_install_suggestion()}\n"
            "（默认目录位于用户 AppData，通常不需要管理员权限。）\n"
            f"原始错误：{exc}"
        )


def _python_runtime_key() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if IS_WINDOWS or system.startswith("win"):
        if machine in {"amd64", "x86_64", "x64"}:
            return "windows-x64"
        if machine in {"arm64", "aarch64"}:
            return "windows-arm64"
        return None
    if system == "linux":
        if machine in {"amd64", "x86_64", "x64"}:
            return "linux-x64"
        if machine in {"arm64", "aarch64"}:
            return "linux-arm64"
    return None


def _python_runtime_source() -> dict[str, str] | None:
    key = _python_runtime_key()
    if key is None:
        return None
    source = dict(PYTHON_RUNTIME_SOURCES[key])
    source["key"] = key
    return source


def _python_is_complete(python: Path) -> bool:
    if not python.is_file():
        return False
    check = (
        "import sys, venv, ensurepip, ssl, sqlite3; "
        "assert sys.version_info >= (3, 10)"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", check],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _find_complete_python() -> Path | None:
    candidates: list[Path] = []
    if not getattr(sys, "frozen", False) and sys.executable:
        candidates.append(Path(sys.executable))
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    py_launcher = shutil.which("py")
    if py_launcher:
        try:
            result = subprocess.run(
                [py_launcher, "-3", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=_creation_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            value = result.stdout.strip().splitlines()
            if value:
                candidates.append(Path(value[-1]))
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        if _python_is_complete(candidate):
            return candidate
    return None


def _portable_runtime_python(install_dir: Path) -> Path | None:
    root = install_dir / PYTHON_RUNTIME_DIRNAME
    for rel in ("python.exe", "bin/python3", "bin/python"):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def has_bundled_python() -> bool:
    return _bundled_python_archive() is not None


def _bundled_python_archive() -> Path | None:
    path = _resource_path(BUNDLED_PYTHON_ARCHIVE_NAME)
    return path if path.is_file() else None


def _bundled_python_expected_hash() -> str:
    hash_file = _resource_path(BUNDLED_PYTHON_HASH_NAME)
    try:
        value = hash_file.read_text(encoding="utf-8").strip().split()[0]
    except (OSError, IndexError):
        value = ""
    if value:
        return value
    source = BUNDLED_PYTHON_SOURCES.get(_python_runtime_key() or "")
    return str(source.get("sha256", "")) if source else ""


def _install_bundled_python(
    install_dir: Path, cancel_event: Any | None = None
) -> Path | None:
    """解压安装器内置的 Python；失败时返回 None 让调用方走网络下载。"""
    archive = _bundled_python_archive()
    if archive is None:
        return None
    expected = _bundled_python_expected_hash()
    if expected and sha256_file(archive).lower() != expected.lower():
        log("内置 Python 校验失败，改用网络下载")
        return None
    _check_cancel(cancel_event)
    stage("解压内置便携 Python…")
    extract_root = install_dir / ".python-runtime-bundled"
    try:
        try:
            extract_archive(archive, extract_root, allow_symlinks=True)
            runtime_root = _locate_runtime_root(extract_root)
            target = install_dir / PYTHON_RUNTIME_DIRNAME
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            runtime_root.rename(target)
        except (OSError, SystemExit) as exc:
            message = getattr(exc, "message", str(exc))
            log(f"内置 Python 解压失败，改用网络下载：{message}")
            return None
    finally:
        shutil.rmtree(extract_root, ignore_errors=True)
    python = _portable_runtime_python(install_dir)
    if python is not None and _python_is_complete(python):
        log(f"已解压内置便携 Python：{python}")
        stage("内置 Python 环境准备完成")
        return python
    log("内置 Python 解压后校验失败，改用网络下载")
    return None


def _locate_runtime_root(extract_root: Path) -> Path:
    candidates = [
        extract_root / "python",
        extract_root,
        *[path for path in extract_root.iterdir() if path.is_dir()],
    ]
    for candidate in candidates:
        if (
            (candidate / "python.exe").is_file()
            or (candidate / "bin" / "python3").is_file()
            or (candidate / "bin" / "python").is_file()
        ):
            return candidate
    fail("便携 Python 压缩包结构异常：找不到 python.exe / bin/python3")


def ensure_python_runtime(
    install_dir: Path,
    *,
    frontend: str,
    cancel_event: Any | None = None,
) -> Path:
    """优先系统 Python；缺失/不完整时下载便携 Python 到安装目录。"""
    stage("检查 Python 环境…")
    existing = _portable_runtime_python(install_dir)
    if existing is not None and _python_is_complete(existing):
        log(f"复用安装目录内的便携 Python：{existing}")
        return existing
    bundled = _install_bundled_python(install_dir, cancel_event)
    if bundled is not None:
        return bundled
    system = _find_complete_python()
    if system is not None:
        log(f"使用系统 Python：{system}")
        return system
    if frontend == "gui" and not IS_WINDOWS:
        fail(
            "系统 Python 3.10+ 不完整，且原生 GUI 还需要系统 PyGObject/GTK4。\n"
            "便携 Python 无法加载系统 PyGObject，请先安装：\n"
            "  sudo apt install python3 python3-venv python3-pip "
            "python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
            "然后重新运行安装器。"
        )
    source = _python_runtime_source()
    if source is None:
        fail(
            "系统 Python 3.10+ 不完整，当前平台也没有可自动下载的便携 Python。\n"
            "请先安装 Python 3.10+ 后重试。"
        )
    stage(
        f"未找到完整的 Python 3.10+，自动下载便携 Python "
        f"{PYTHON_RUNTIME_VERSION}（{source['label']}）…"
    )
    archive = install_dir / f".python-runtime-{PYTHON_RUNTIME_VERSION}.tar.gz"
    extract_root = install_dir / ".python-runtime-extract"
    try:
        download(
            source["url"],
            archive,
            expected_sha256=source["sha256"],
            cancel_event=cancel_event,
        )
        extract_archive(archive, extract_root, allow_symlinks=True)
        runtime_root = _locate_runtime_root(extract_root)
        target = install_dir / PYTHON_RUNTIME_DIRNAME
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        runtime_root.rename(target)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(extract_root, ignore_errors=True)
    python = _portable_runtime_python(install_dir)
    if python is None or not _python_is_complete(python):
        fail("便携 Python 下载完成但校验失败；请重试或手动安装 Python 3.10+。")
    log(f"便携 Python 已安装：{python}（未改动系统 Python）")
    stage("Python 环境准备完成")
    return python


# ---------------------------------------------------------------------------
# 镜像测速
# ---------------------------------------------------------------------------


def load_windows_package_contract() -> dict[str, Any]:
    """读取 Windows GUI 预构建包契约；缺失时使用内置默认值。"""
    global _PACKAGE_CONTRACT_CACHE
    if _PACKAGE_CONTRACT_CACHE is not None:
        return _PACKAGE_CONTRACT_CACHE
    contract = dict(DEFAULT_WINDOWS_PACKAGE)
    try:
        data = json.loads(WINDOWS_PACKAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"读取 Windows GUI 包契约失败，使用内置默认值：{exc}")
    else:
        if isinstance(data, dict):
            contract.update(data)
    _PACKAGE_CONTRACT_CACHE = contract
    return contract


def windows_gui_asset_name() -> str:
    name = str(
        load_windows_package_contract().get("asset_name") or WINDOWS_GUI_ASSET_NAME
    ).strip()
    return name or WINDOWS_GUI_ASSET_NAME


def load_mirrors() -> list[dict[str, Any]]:
    global _MIRROR_CACHE
    if _MIRROR_CACHE is not None:
        return _MIRROR_CACHE
    try:
        data = json.loads(MIRRORS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"读取镜像列表失败，使用 GitHub 直连：{exc}")
        _MIRROR_CACHE = [{"name": "direct", "kind": "direct", "enabled": True}]
        return _MIRROR_CACHE
    mirrors: list[dict[str, Any]] = []
    for item in data.get("mirrors", []):
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        kind = str(item.get("kind") or "direct")
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if kind == "prefix" and not str(item.get("prefix") or "").strip():
            continue
        mirrors.append(item)
    if not any(item.get("kind") == "direct" for item in mirrors):
        mirrors.insert(0, {"name": "direct", "kind": "direct", "enabled": True})
    _MIRROR_CACHE = mirrors
    return _MIRROR_CACHE


def set_mirror_override(name: str) -> None:
    """固定使用某个镜像；auto/空字符串表示自动选择。"""
    global _MIRROR_OVERRIDE
    value = str(name or "").strip()
    if not value or value.lower() == "auto":
        _MIRROR_OVERRIDE = None
        return
    names = {str(item.get("name")) for item in load_mirrors()}
    if value not in names:
        fail(f"未知镜像：{value}；可用镜像：{', '.join(sorted(names))}")
    _MIRROR_OVERRIDE = value


def available_mirrors() -> list[dict[str, Any]]:
    mirrors = load_mirrors()
    override = _MIRROR_OVERRIDE or os.environ.get("KAREN_INSTALL_MIRROR", "").strip()
    if not override or override.lower() == "auto":
        return mirrors
    chosen = [item for item in mirrors if item.get("name") == override]
    if not chosen:
        fail(f"未知镜像：{override}")
    return chosen


def mirrored_url(url: str, mirror: dict[str, Any]) -> str:
    if mirror.get("kind") != "prefix":
        return url
    prefix = str(mirror.get("prefix") or "").rstrip("/")
    if not prefix:
        return url
    suffix = url
    for base in ("https://github.com", "http://github.com"):
        if url.startswith(base):
            suffix = url[len(base) :]
            break
    if suffix.startswith("/"):
        return prefix + suffix
    return prefix + "/" + suffix


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> urllib.request.Request:
    values = {"User-Agent": USER_AGENT}
    if headers:
        values.update(headers)
    return urllib.request.Request(url, headers=values)


def mirror_score(speed: float, latency: float) -> float:
    """吞吐为主，首字节延迟作为惩罚项；避免只看速度选到高延迟镜像。"""
    return max(float(speed), 0.0) / (1.0 + max(float(latency), 0.0))


def probe_mirror(
    url: str, mirror: dict[str, Any], *, timeout: float = PROBE_TIMEOUT
) -> dict[str, Any]:
    target = mirrored_url(url, mirror)
    started = time.perf_counter()
    try:
        request = _request(
            target,
            headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"},
            timeout=timeout,
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            first_byte = time.perf_counter() - started
            chunks: list[bytes] = []
            remaining = PROBE_BYTES
            while remaining > 0:
                chunk = response.read(min(DOWNLOAD_CHUNK, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        data = b"".join(chunks)
        if not data:
            raise OSError("响应为空")
        head = data[:512].lstrip().lower()
        if head.startswith((b"<!doctype", b"<html")):
            raise OSError("镜像返回 HTML 错误页")
        elapsed = max(time.perf_counter() - started, 0.001)
        speed = len(data) / elapsed
        return {
            "ok": True,
            "mirror": mirror.get("name", "?"),
            "url": target,
            "latency": first_byte,
            "bytes": len(data),
            "elapsed": elapsed,
            "speed": speed,
            "score": mirror_score(speed, first_byte),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "mirror": mirror.get("name", "?"),
            "url": target,
            "error": f"{type(exc).__name__}: {exc}",
            "speed": 0.0,
            "latency": 999.0,
            "score": 0.0,
        }


def probe_mirrors(
    url: str,
    mirrors: list[dict[str, Any]] | None = None,
    *,
    timeout: float = PROBE_TIMEOUT,
) -> list[dict[str, Any]]:
    """并发探测所有镜像，返回完整结果（含失败项），供 GUI 表格展示。"""
    candidates = mirrors if mirrors is not None else available_mirrors()
    if not candidates:
        return []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(PROBE_WORKERS, len(candidates))
    ) as pool:
        return list(
            pool.map(
                lambda item: probe_mirror(url, item, timeout=timeout),
                candidates,
            )
        )


def rank_mirrors(
    url: str,
    mirrors: list[dict[str, Any]] | None = None,
    *,
    timeout: float = PROBE_TIMEOUT,
) -> list[dict[str, Any]]:
    log(f"测速 {url}")
    results = probe_mirrors(url, mirrors, timeout=timeout)
    good = [item for item in results if item.get("ok")]
    good.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            -float(item.get("speed") or 0),
            float(item.get("latency") or 999),
        )
    )
    for item in sorted(
        results,
        key=lambda result: (
            not bool(result.get("ok")),
            -float(result.get("score") or 0),
        ),
    ):
        if item.get("ok"):
            log(
                f"  {item['mirror']}: {item['speed'] / 1024:.1f} KB/s, "
                f"首字节 {item['latency']:.2f}s, 评分 {item['score'] / 1024:.1f}"
            )
        else:
            log(f"  {item['mirror']}: 失败 {item.get('error')}")
    if not good:
        log("没有可用镜像；如果设置了 --mirror，请换一个镜像。")
    return good


# ---------------------------------------------------------------------------
# 下载与解压
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    destination: Path,
    *,
    expected_sha256: str = "",
    cancel_event: Any | None = None,
    ranked: list[dict[str, Any]] | None = None,
    mirrors: list[dict[str, Any]] | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if ranked is None:
        stage(f"自动测速并选择镜像：{url}")
        candidates = rank_mirrors(url, mirrors)
    else:
        candidates = ranked
    if not candidates:
        stage("GitHub 镜像测速失败")
        fail(
            "GitHub 镜像测速全部失败。请检查网络、代理、DNS 或防火墙；\n"
            "如果使用代理，请确认 HTTPS_PROXY/HTTP_PROXY 配置正确。\n"
            "也可以稍后重试，或用 --mirror direct 固定 GitHub 直连。"
        )
    errors: list[str] = []
    for candidate in candidates:
        target = str(candidate.get("url") or "")
        if not target:
            continue
        temp = destination.with_name(destination.name + ".part")
        try:
            _check_cancel(cancel_event)
            log(f"下载 {target}")
            with urllib.request.urlopen(
                _request(target), timeout=DOWNLOAD_TIMEOUT
            ) as response, temp.open("wb") as handle:
                total = 0
                first_chunk = True
                length = response.headers.get("Content-Length")
                expected_total = int(length) if length and length.isdigit() else None
                _emit_progress(0, expected_total)
                last_report = 0.0
                while True:
                    _check_cancel(cancel_event)
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    if first_chunk:
                        first_chunk = False
                        head = chunk[:512].lstrip().lower()
                        if head.startswith((b"<!doctype", b"<html")):
                            raise OSError("镜像返回 HTML 错误页")
                    handle.write(chunk)
                    total += len(chunk)
                    _emit_progress(total, expected_total)
                    now = time.monotonic()
                    if now - last_report >= 1.0:
                        log(f"  已下载 {total / 1024 / 1024:.1f} MB")
                        last_report = now
            if total <= 0:
                raise OSError("下载内容为空")
            if expected_total is not None and total != expected_total:
                raise OSError(
                    f"下载不完整：{total} / {expected_total} 字节"
                )
            if expected_sha256:
                actual = sha256_file(temp)
                if actual.lower() != expected_sha256.lower():
                    temp.unlink(missing_ok=True)
                    errors.append(f"{candidate.get('mirror', '?')}: SHA256 不匹配")
                    continue
            temp.replace(destination)
            log(
                f"下载完成：{destination}"
                f"（{destination.stat().st_size / 1024 / 1024:.1f} MB）"
            )
            return destination
        except InstallCancelled:
            temp.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001
            temp.unlink(missing_ok=True)
            errors.append(
                f"{candidate.get('mirror', '?')}: {type(exc).__name__}: {exc}"
            )
    fail(
        "下载失败：\n"
        + "\n".join(errors)
        + "\n\n如果错误都是超时/连接失败，请检查网络、代理、DNS 或防火墙；"
        + "如果使用代理，请确认 HTTPS_PROXY/HTTP_PROXY 配置正确。"
    )


def download_from_sources(
    sources: Iterable[str],
    destination: Path,
    *,
    expected_sha256: str = "",
    cancel_event: Any | None = None,
) -> Path:
    """按顺序尝试多个直链（字体等非 github.com 资产）。"""
    errors: list[str] = []
    for source in sources:
        _check_cancel(cancel_event)
        try:
            return download(
                source,
                destination,
                expected_sha256=expected_sha256,
                cancel_event=cancel_event,
                ranked=[{"name": "direct", "mirror": "direct", "url": source}],
            )
        except InstallCancelled:
            raise
        except SystemExit as exc:
            message = getattr(exc, "message", str(exc))
            errors.append(f"{source}: {message}")
            log(f"  该来源失败，尝试下一个：{message.splitlines()[0]}")
    fail("所有下载来源都失败：\n" + "\n".join(errors))


def _safe_tar_extract(
    archive: Path, destination: Path, *, allow_symlinks: bool = False
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                fail(f"压缩包包含越界路径：{member.name}")
            if member.issym() or member.islnk():
                if not allow_symlinks:
                    fail(f"压缩包包含链接，拒绝解压：{member.name}")
                link = Path(member.linkname)
                if link.is_absolute():
                    fail(f"压缩包包含绝对链接，拒绝解压：{member.name}")
                base = (
                    destination / Path(member.name).parent
                    if member.issym()
                    else destination
                )
                link_target = (base / link).resolve()
                if link_target != root and root not in link_target.parents:
                    fail(f"压缩包链接越界，拒绝解压：{member.name}")
            if member.isdev():
                fail(f"压缩包包含设备文件，拒绝解压：{member.name}")
        tar.extractall(destination)


def _safe_zip_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                fail(f"压缩包包含越界路径：{info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                fail(f"压缩包包含符号链接，拒绝解压：{info.filename}")
        zf.extractall(destination)


def extract_archive(
    archive: Path, destination: Path, *, allow_symlinks: bool = False
) -> None:
    if archive.suffix.lower() in {".zip"}:
        _safe_zip_extract(archive, destination)
    else:
        _safe_tar_extract(
            archive, destination, allow_symlinks=allow_symlinks
        )


# ---------------------------------------------------------------------------
# 安装包识别
# ---------------------------------------------------------------------------

_PREBUILT_NAMES = {
    "gui": ("karenbot.exe", "karenbot-gui.exe", "karen-gui.exe"),
    "webui": ("karenbot.exe", "karenbot-webui.exe", "karen-webui.exe"),
}


def _prebuilt_executable(app_root: Path, frontend: str | None = None) -> Path | None:
    """识别预构建应用包（Windows .exe / Linux 无后缀可执行文件）。"""
    if not app_root.is_dir():
        return None
    names = set(_PREBUILT_NAMES.get(frontend or "", ()))
    names.update(_PREBUILT_NAMES["gui"])
    names.update(_PREBUILT_NAMES["webui"])
    for path in app_root.iterdir():
        if not path.is_file():
            continue
        lowered = path.name.casefold()
        if lowered in names:
            return path
        if IS_WINDOWS and lowered.endswith(".exe"):
            stem = lowered[:-4]
            if stem in {name[:-4] for name in names}:
                return path
    return None


def _portable_python(app_root: Path) -> Path | None:
    """识别 Windows GTK4 便携包里的 Python 运行时。"""
    if not app_root.is_dir():
        return None
    for rel in load_windows_package_contract().get("python_candidates", []):
        candidate = app_root / str(rel)
        if candidate.is_file():
            return candidate
    return None


def _find_app_root(directory: Path) -> Path:
    candidates = [directory, *[p for p in directory.iterdir() if p.is_dir()]]
    for candidate in candidates:
        if (candidate / "backend").is_dir() and (
            (candidate / "gui").is_dir() or (candidate / "webui").is_dir()
        ):
            return candidate
        if _prebuilt_executable(candidate) is not None:
            return candidate
        if _portable_python(candidate) is not None:
            return candidate
    fail("安装包中找不到 backend/ + gui|webui/，也没有可识别的 KarenBot 可执行文件")


def _find_core_root(directory: Path) -> Path:
    """定位 backend Release 的“核心程序 + 后端”根目录。"""
    candidates = [directory, *[p for p in directory.iterdir() if p.is_dir()]]
    for candidate in candidates:
        if (
            (candidate / "main.py").is_file()
            and (candidate / "backend").is_dir()
            and (candidate / "core").is_dir()
        ):
            return candidate
    fail("backend 安装包中找不到 main.py + backend/ + core/，不是有效的核心程序包")


def _find_frontend_root(directory: Path, frontend: str) -> Path:
    """定位 GUI/WebUI Release 的模块根目录。"""
    candidates = [directory, *[p for p in directory.iterdir() if p.is_dir()]]
    for candidate in candidates:
        if (candidate / frontend / "plugin.json").is_file():
            return candidate
    fail(f"{frontend} 安装包中找不到 {frontend}/plugin.json")


def _validate_portable_gui(app_root: Path, python: Path) -> None:
    """按 windows-gui-package.json 契约检查 GTK4 运行时。"""
    if not IS_WINDOWS:
        log("非 Windows 环境，跳过 GTK4 运行时 DLL 检查")
        return
    contract = load_windows_package_contract()
    for rel in contract.get("required_dirs", []):
        if not (app_root / str(rel)).is_dir():
            fail(f"Windows GUI 预构建包缺少目录：{rel}")
    dll_dirs = [python.parent, app_root / "runtime" / "bin", app_root / "bin"]
    found: set[str] = set()
    for directory in dll_dirs:
        if not directory.is_dir():
            continue
        found.update(path.name.casefold() for path in directory.iterdir() if path.is_file())
    missing = [
        str(name)
        for name in contract.get("required_dlls", [])
        if str(name).casefold() not in found
    ]
    if missing:
        fail(
            "Windows GUI 预构建包缺少 GTK4/Adwaita 运行时 DLL：\n  "
            + "\n  ".join(missing)
            + "\n请按 docs/windows-gui-package.md 的契约重新打包。"
        )
    typelib_root: Path | None = None
    for rel in contract.get("typelib_dirs", []):
        candidate = app_root / str(rel)
        if candidate.is_dir():
            typelib_root = candidate
            break
    if typelib_root is None:
        fail(
            "Windows GUI 预构建包缺少 GObject Introspection typelib 目录；"
            "至少需要 Gtk-4.0.typelib 和 Adw-1.typelib。"
        )
    typelibs = {path.name.casefold() for path in typelib_root.iterdir() if path.is_file()}
    missing_typelibs = [
        str(name)
        for name in contract.get("required_typelibs", [])
        if str(name).casefold() not in typelibs
    ]
    if missing_typelibs:
        fail(
            "Windows GUI 预构建包缺少 typelib：\n  "
            + "\n  ".join(missing_typelibs)
            + "\n请按 docs/windows-gui-package.md 的契约重新打包。"
        )
    log("Windows GUI 便携运行时检查通过（GTK4 + libadwaita + PyGObject）")


# ---------------------------------------------------------------------------
# GitHub Release 解析
# ---------------------------------------------------------------------------


def _validate_online_tag(value: str) -> str:
    tag = str(value or "").strip()
    if not re.fullmatch(r"v?[0-9A-Za-z][0-9A-Za-z._-]{0,63}", tag):
        fail(f"无效的 Release tag：{value!r}")
    return tag


def _online_archive_url(repo: str, tag: str) -> str:
    return ONLINE_ARCHIVE_TEMPLATE.format(repo=repo, tag=tag)


def _fetch_github_page(
    repo: str, path: str, *, timeout: float = 20
) -> tuple[bytes, str]:
    """按 direct → 镜像顺序抓取 GitHub 页面，返回 (内容, 最终 URL)。"""
    url = f"https://github.com/{repo}/{path}"
    errors: list[str] = []
    for mirror in available_mirrors():
        target = mirrored_url(url, mirror)
        try:
            with urllib.request.urlopen(
                _request(target), timeout=timeout
            ) as response:
                data = response.read(2 * 1024 * 1024)
                return data, response.geturl()
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"{mirror.get('name', '?')}: {type(exc).__name__}: {exc}"
            )
    fail(
        "无法访问 GitHub：\n"
        + "\n".join(errors)
        + "\n请检查网络、代理、DNS 或防火墙；如果使用代理，请确认 "
        + "HTTPS_PROXY/HTTP_PROXY 配置正确。"
    )


def _release_tag_from_atom(data: bytes) -> str:
    """从 GitHub releases.atom 的第一条记录取 tag。"""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        fail(f"GitHub Release feed 解析失败：{exc}")
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        link = entry.find("atom:link", ns)
        href = str(link.attrib.get("href") or "") if link is not None else ""
        match = re.search(r"/releases/tag/([0-9A-Za-z._-]+)", href)
        if match:
            tag = unquote(match.group(1))
            return _validate_online_tag(tag)
    fail("GitHub Release feed 中没有可用的版本")


def _latest_release_tag(
    repo: str, *, cancel_event: Any | None = None
) -> tuple[str, str]:
    """优先 GitHub 的 latest 稳定版，否则用 releases.atom 的最新版本。

    不使用 GitHub API，避免未登录时的 60 次/小时限流；页面和 feed 都走
    direct → 镜像回退。
    """
    _check_cancel(cancel_event)
    try:
        data, final_url = _fetch_github_page(repo, "releases/latest")
        haystack = final_url + "\n" + data.decode("utf-8", "ignore")
        match = re.search(r"/releases/tag/([0-9A-Za-z._-]+)", haystack)
        if match:
            tag = _validate_online_tag(unquote(match.group(1)))
            log(f"{repo} 选择版本：{tag}（稳定版）")
            return tag, "稳定版"
    except SystemExit as exc:
        message = getattr(exc, "message", str(exc))
        log(f"{repo} 稳定版检查失败，改查 releases.atom：{message.splitlines()[0]}")
    _check_cancel(cancel_event)
    data, _final_url = _fetch_github_page(repo, "releases.atom")
    tag = _release_tag_from_atom(data)
    log(f"{repo} 未找到稳定版，选择最新 Release：{tag}")
    return tag, "最新版"


def _resolve_online_tags(
    frontend: str,
    requested_tag: str,
    *,
    cancel_event: Any | None = None,
) -> tuple[str, str]:
    """返回 (backend_tag, frontend_tag)。

    默认每个仓库优先最新稳定版；没有稳定版时选择最新 Release。
    显式 --tag 时两个仓库使用同一个 tag。
    """
    requested = str(requested_tag or "").strip()
    if requested and requested.lower() not in {"auto", "latest"}:
        tag = _validate_online_tag(requested)
        log(f"使用指定 Release 版本：{tag}")
        return tag, tag

    frontend_repo = ONLINE_FRONTEND_REPOS[frontend]
    backend_tag, backend_kind = _latest_release_tag(
        ONLINE_BACKEND_REPO, cancel_event=cancel_event
    )
    _check_cancel(cancel_event)
    frontend_tag, frontend_kind = _latest_release_tag(
        frontend_repo, cancel_event=cancel_event
    )
    log(
        f"在线版本：backend={backend_tag}（{backend_kind}），"
        f"{frontend}={frontend_tag}（{frontend_kind}）"
    )
    return backend_tag, frontend_tag


def _windows_gui_asset_url(tag: str) -> str:
    repo = WINDOWS_BUNDLE_REPO
    name = windows_gui_asset_name()
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def _windows_bundle_tag(
    bundle_tag: str, cancel_event: Any | None
) -> tuple[str, str]:
    requested = str(bundle_tag or "").strip()
    if requested and requested.lower() not in {"", "auto", "latest"}:
        return _validate_online_tag(requested), "指定版本"
    tag, kind = _latest_release_tag(
        WINDOWS_BUNDLE_REPO, cancel_event=cancel_event
    )
    log(f"Windows GUI 便携包版本：{tag}（{kind}）")
    return tag, kind


def _prepare_windows_gui_app(
    staging: Path,
    *,
    cancel_event: Any | None,
    bundle_tag: str = "auto",
    windows_gui_asset: str = "",
) -> tuple[str, dict[str, Any]]:
    """下载安装器 Release 里的 Windows GTK4 便携包。"""
    asset_name = windows_gui_asset_name()
    override = str(windows_gui_asset or "").strip()
    if not override:
        override = os.environ.get(WINDOWS_GUI_ASSET_ENV, "").strip()
    if override:
        bundle_version = "custom"
        bundle_kind = "自定义"
        asset_url = override
    else:
        bundle_version, bundle_kind = _windows_bundle_tag(
            bundle_tag, cancel_event
        )
        asset_url = _windows_gui_asset_url(bundle_version)
    downloads = staging / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    stage(f"自动测速并下载 Windows GUI 便携包：{asset_name}")
    local_asset = Path(asset_url).expanduser()
    if "://" not in asset_url and local_asset.is_file():
        archive = downloads / asset_name
        shutil.copy2(local_asset, archive)
        log(f"使用本地 Windows GUI 便携包：{local_asset}")
    else:
        ranked = None
        if not asset_url.startswith("https://github.com/"):
            ranked = [{"name": "direct", "mirror": "direct", "url": asset_url}]
        try:
            archive = download(
                asset_url,
                downloads / asset_name,
                cancel_event=cancel_event,
                ranked=ranked,
            )
        except SystemExit as exc:
            message = getattr(exc, "message", str(exc))
            fail(
                "Windows 原生 GUI 需要安装器仓库 Release 中的预构建包：\n"
                f"  {asset_url}\n"
                "当前 Release 可能还没有该资产，或网络不可用。\n"
                "可用 KAREN_GUI_WINDOWS_ASSET=<本地/远程 zip> 覆盖，"
                "打包契约见 docs/windows-gui-package.md。\n"
                f"原始错误：{message}"
            )
    _check_cancel(cancel_event)
    extract_root = staging / "extract-windows-gui"
    extract_archive(archive, extract_root)
    app_root = _find_app_root(extract_root)
    shutil.copytree(app_root, staging / "app", ignore=_IGNORE)
    portable = _portable_python(staging / "app")
    if portable is None:
        fail(
            "Windows GUI 预构建包中没有找到 runtime/python.exe；"
            "请按 docs/windows-gui-package.md 的目录契约打包。"
        )
    stage("校验 GTK4 便携运行时…")
    _validate_portable_gui(staging / "app", portable)
    stage("Windows GUI 便携运行时校验通过")
    bundle_manifest: dict[str, Any] = {}
    manifest_path = staging / "app" / "windows-bundle.json"
    try:
        bundle_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        log("便携包没有 windows-bundle.json，组件版本将标记为 unknown")
    components: dict[str, Any] = {
        "backend": bundle_manifest.get(
            "backend", {"repo": ONLINE_BACKEND_REPO, "tag": "unknown"}
        ),
        "gui": bundle_manifest.get(
            "gui",
            {
                "repo": ONLINE_FRONTEND_REPOS["gui"],
                "tag": "unknown",
            },
        ),
        "runtime": bundle_manifest.get(
            "runtime", {"kind": "windows-gtk4-portable"}
        ),
        "bundle": {
            "repo": WINDOWS_BUNDLE_REPO,
            "tag": bundle_version,
            "kind": bundle_kind,
            "asset": asset_name,
            "asset_url": asset_url,
        },
    }
    return "windows-portable-release", components


def _prepare_online_app(
    staging: Path,
    *,
    frontend: str,
    release_tag: str,
    cancel_event: Any | None,
    bundle_tag: str = "auto",
    windows_gui_asset: str = "",
) -> tuple[str, dict[str, Any]]:
    """下载并合并 backend 核心包与所选界面模块，返回 installer_kind/components。"""
    if frontend not in ONLINE_FRONTEND_REPOS:
        fail(f"不支持的界面：{frontend}")
    if IS_WINDOWS and frontend == "gui":
        return _prepare_windows_gui_app(
            staging,
            cancel_event=cancel_event,
            bundle_tag=bundle_tag,
            windows_gui_asset=windows_gui_asset,
        )
    backend_tag, frontend_tag = _resolve_online_tags(
        frontend,
        release_tag,
        cancel_event=cancel_event,
    )
    backend_url = _online_archive_url(ONLINE_BACKEND_REPO, backend_tag)
    frontend_repo = ONLINE_FRONTEND_REPOS[frontend]
    frontend_url = _online_archive_url(frontend_repo, frontend_tag)
    downloads = staging / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    stage("自动测速并下载 backend 核心包…")
    backend_archive = download(
        backend_url,
        downloads / "backend.tar.gz",
        cancel_event=cancel_event,
    )
    _check_cancel(cancel_event)
    stage(f"自动测速并下载 {frontend.upper()} 界面模块…")
    frontend_archive = download(
        frontend_url,
        downloads / "frontend.tar.gz",
        cancel_event=cancel_event,
    )
    _check_cancel(cancel_event)

    backend_extract = staging / "extract-backend"
    frontend_extract = staging / "extract-frontend"
    extract_archive(backend_archive, backend_extract)
    extract_archive(frontend_archive, frontend_extract)
    core_root = _find_core_root(backend_extract)
    frontend_root = _find_frontend_root(frontend_extract, frontend)
    app_root = staging / "app"
    stage("合并核心程序、后端和界面模块…")
    shutil.copytree(core_root, app_root, ignore=_IGNORE)
    frontend_target = app_root / frontend
    if frontend_target.exists():
        shutil.rmtree(frontend_target)
    shutil.copytree(
        frontend_root / frontend,
        frontend_target,
        ignore=_IGNORE,
    )
    components = {
        "backend": {
            "repo": ONLINE_BACKEND_REPO,
            "dir": "backend",
            "tag": backend_tag,
            "version": backend_tag.lstrip("v"),
        },
        frontend: {
            "repo": frontend_repo,
            "dir": frontend,
            "tag": frontend_tag,
            "version": frontend_tag.lstrip("v"),
        },
    }
    return "online-release", components


_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "data",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".karen-run",
    ".debata-run",
    "build",
    "dist",
    "release",
    "module_backups",
    "backups",
    "*.log",
)


# ---------------------------------------------------------------------------
# 依赖 / 字体 / 主题
# ---------------------------------------------------------------------------


def _validate_frontend(app_root: Path, frontend: str) -> None:
    required = ["backend"] + (["gui"] if frontend == "gui" else ["webui"])
    missing = [name for name in required if not (app_root / name).is_dir()]
    if missing:
        fail(f"安装源缺少目录：{', '.join(missing)}")


def _venv_python(venv: Path) -> Path:
    if IS_WINDOWS:
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _create_venv(
    venv: Path, *, frontend: str, base_python: Path
) -> None:
    if venv.exists():
        log(f"复用已有 venv：{venv}")
        return
    uv = shutil.which("uv")
    if uv:
        command = [uv, "venv", "--python", str(base_python), str(venv)]
        if frontend == "gui" and not IS_WINDOWS:
            command.insert(2, "--system-site-packages")
        run(command)
        return
    command = [str(base_python), "-m", "venv", str(venv)]
    if frontend == "gui" and not IS_WINDOWS:
        command.insert(3, "--system-site-packages")
    run(command)


def _install_dependencies(venv: Path, app_root: Path) -> None:
    python = _venv_python(venv)
    uv = shutil.which("uv")
    if uv:
        run([uv, "pip", "install", "--python", str(python), "-e", str(app_root)])
    else:
        run([str(python), "-m", "pip", "install", "-e", str(app_root)])


def _check_gui_runtime(venv: Path) -> None:
    if IS_WINDOWS:
        fail(
            "Windows 源码安装不支持原生 GUI：GTK4/PyGObject 需要预构建便携包。\n"
            "请选择在线安装（下载安装器 Release 的 Windows 便携包），"
            "或使用本地预构建 zip。"
        )
    python = _venv_python(venv)
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')",
        ],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )
    if result.returncode != 0:
        fail(
            "GUI 需要系统 GTK4/libadwaita/PyGObject。Debian/Ubuntu 请安装：\n"
            "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
            f"检测输出：{result.stderr.strip()}"
        )


def _font_dir() -> Path:
    if IS_WINDOWS:
        base = Path(
            os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        )
        return base / "KarenBot" / "fonts"
    data_home = Path(
        os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    )
    return data_home / "KarenBot" / "fonts"


def _fontconfig_path() -> Path:
    return _font_dir() / "fonts.conf"


def _write_fontconfig(root: Path) -> None:
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        windows_root = str(os.environ.get("WINDIR") or r"C:\Windows").replace(
            "\\", "/"
        )
        windows_fonts = Path(windows_root) / "Fonts"
        include = f"  <dir>{xml_escape(windows_fonts.as_posix())}</dir>\n"
    else:
        include = '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
    (root / "fonts.conf").write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        "<fontconfig>\n"
        f"{include}"
        f"  <dir>{xml_escape(root.as_posix())}</dir>\n"
        f"  <cachedir>{xml_escape(cache.as_posix())}</cachedir>\n"
        "</fontconfig>\n",
        encoding="utf-8",
    )


def install_fonts(cancel_event: Any | None = None) -> None:
    """下载 GUI 实际使用的 Noto Sans SC / Noto Serif SC 可变字体。"""
    root = _font_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"字体目录不可写，跳过字体下载：{exc}")
        return
    missing: list[str] = []
    for item in FONT_SOURCES:
        _check_cancel(cancel_event)
        name = str(item["name"])
        target = root / name
        expected = str(item["sha256"])
        if target.is_file():
            if sha256_file(target).lower() == expected.lower():
                log(f"字体已存在且校验通过：{name}")
                continue
            log(f"字体校验失败，重新下载：{name}")
            target.unlink(missing_ok=True)
        try:
            download_from_sources(
                item["urls"],
                target,
                expected_sha256=expected,
                cancel_event=cancel_event,
            )
        except InstallCancelled:
            raise
        except SystemExit as exc:
            log(f"字体下载失败（可选）：{name}；{getattr(exc, 'message', exc)}")
            missing.append(name)
    _write_fontconfig(root)
    log(f"字体目录：{root}")
    if missing:
        log("缺少字体：" + ", ".join(missing) + "；GUI 会回退到系统字体。")


def _gui_skin_dirs() -> list[Path]:
    """GUI 的 theme.py 使用 GLib 用户配置目录；返回所有可能位置。"""
    bases: list[Path] = []
    if IS_WINDOWS:
        for env_name, fallback in (
            ("APPDATA", Path.home() / "AppData" / "Roaming"),
            ("LOCALAPPDATA", Path.home() / "AppData" / "Local"),
        ):
            bases.append(Path(os.environ.get(env_name) or fallback))
    else:
        bases.append(
            Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        )
    result: list[Path] = []
    for base in bases:
        path = base / "karen-gui"
        if path not in result:
            result.append(path)
    return result


def apply_gui_skin(skin: str) -> None:
    """写入 GUI 的 skin 偏好；karen/system 都是 GTK4 内置皮肤。"""
    value = str(skin or "none").strip()
    if value in {"", "none"}:
        if IS_WINDOWS:
            log(
                "未应用主题包；Windows 下 GTK4 默认外观可能较差，"
                "需要用户自行解决。"
            )
        else:
            log("未应用主题包；GUI 将使用系统 GTK4 外观。")
        return
    if value not in {"karen", "system"}:
        fail(f"未知主题包：{skin}")
    written: list[Path] = []
    for directory in _gui_skin_dirs():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "skin").write_text(value + "\n", encoding="utf-8")
            written.append(directory / "skin")
        except OSError as exc:
            log(f"主题偏好写入失败（{directory}）：{exc}")
    if written:
        log(f"已应用 GUI 主题包：{value}（{GUI_SKINS.get(value, value)}）")
    else:
        log("主题偏好没有写入成功；可在 GUI 设置里手动选择皮肤。")


# ---------------------------------------------------------------------------
# 启动器与快捷方式
# ---------------------------------------------------------------------------


def _font_env(fontconf: Path | None) -> dict[str, str]:
    if fontconf is not None and fontconf.is_file():
        return {"FONTCONFIG_FILE": str(fontconf)}
    return {}


def _render_env(mode: str) -> dict[str, str]:
    if mode == "cairo":
        return {
            "KAREN_GUI_RENDERER": "cairo",
            "GSK_RENDERER": "cairo",
            "GDK_DEBUG": "gl-disable",
            "LIBGL_ALWAYS_SOFTWARE": "1",
        }
    if mode == "gl":
        return {"KAREN_GUI_RENDERER": "gl"}
    return {}


def _portable_env(python: Path) -> dict[str, str]:
    prefix = python.parent.parent
    return {
        "PATH": str(python.parent),
        "XDG_DATA_DIRS": str(prefix / "share"),
        "GI_TYPELIB_PATH": str(prefix / "lib" / "girepository-1.0"),
        "GSETTINGS_SCHEMA_DIR": str(prefix / "share" / "glib-2.0" / "schemas"),
        "GTK_DATA_PREFIX": str(prefix),
        "GTK_EXE_PREFIX": str(prefix),
        "GDK_PIXBUF_MODULEDIR": str(
            prefix / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders"
        ),
        "GDK_PIXBUF_MODULE_FILE": str(
            prefix / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders.cache"
        ),
        "GIO_MODULE_DIR": str(prefix / "lib" / "gio" / "modules"),
    }


def _linux_launcher(
    app_root: Path,
    command: list[str],
    env: dict[str, str],
    *,
    safe: bool,
) -> str:
    lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    for key, value in env.items():
        if key == "PATH":
            lines.append(f'export PATH={shlex.quote(value)}:"$PATH"')
        elif safe:
            lines.append(f"export {key}={shlex.quote(value)}")
        else:
            lines.append(
                f'if [ -z "${{{key}:-}}" ]; then export {key}={shlex.quote(value)}; fi'
            )
    if safe:
        lines.append(
            'if [ -n "${DISPLAY:-}" ] && [ -z "${GDK_BACKEND:-}" ]; then '
            "export GDK_BACKEND=x11; fi"
        )
    lines.append(f"cd {shlex.quote(str(app_root))}")
    lines.append(
        "exec " + " ".join(shlex.quote(part) for part in command) + ' "$@"'
    )
    return "\n".join(lines) + "\n"


def _windows_launcher(
    app_root: Path,
    command: str,
    env: dict[str, str],
    *,
    safe: bool,
) -> str:
    lines = ["@echo off", "chcp 65001 >nul", "setlocal", f'cd /d "{app_root}"']
    for key, value in env.items():
        if key == "PATH":
            lines.append(f'set "PATH={value};%PATH%"')
        elif safe:
            lines.append(f'set "{key}={value}"')
        else:
            lines.append(f'if not defined {key} set "{key}={value}"')
    if safe and IS_WINDOWS:
        lines.append('if not defined GDK_BACKEND set "GDK_BACKEND=win32"')
    lines.append(command + " %*")
    return "\r\n".join(lines) + "\r\n"


def _prepare_windows_portable_runtime(
    app_root: Path, cancel_event: Any | None = None
) -> None:
    """按最终安装路径生成 gdk-pixbuf loaders.cache，避免绝对路径失效。"""
    runtime = app_root / "runtime"
    tool = runtime / "bin" / "gdk-pixbuf-query-loaders.exe"
    module_dir = runtime / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders"
    cache = module_dir.parent / "loaders.cache"
    if not tool.is_file() or not module_dir.is_dir():
        log("便携包没有 gdk-pixbuf-query-loaders，跳过图片加载器缓存")
        return
    _check_cancel(cancel_event)
    env = os.environ.copy()
    env["PATH"] = str(runtime / "bin") + os.pathsep + env.get("PATH", "")
    env["GDK_PIXBUF_MODULEDIR"] = str(module_dir)
    try:
        result = subprocess.run(
            [str(tool)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"生成图片加载器缓存失败（可忽略）：{exc}")
        return
    if result.returncode != 0 or not result.stdout.strip():
        log(
            "生成图片加载器缓存失败（可忽略）："
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )
        return
    cache.write_text(result.stdout, encoding="utf-8")
    log(f"图片加载器缓存：{cache}")


def _write_launchers(
    install_dir: Path,
    app_root: Path,
    venv: Path,
    frontend: str,
    *,
    prebuilt_exe: Path | None = None,
    portable_python: Path | None = None,
    render_mode: str = "auto",
    fontconf: Path | None = None,
) -> None:
    env = _font_env(fontconf) | _render_env(render_mode)
    safe_env = _font_env(fontconf) | _render_env("cairo")
    if portable_python is not None:
        env = _portable_env(portable_python) | env
        safe_env = _portable_env(portable_python) | safe_env
    if IS_WINDOWS:
        _write_windows_launchers(
            install_dir,
            app_root,
            venv,
            frontend,
            prebuilt_exe=prebuilt_exe,
            portable_python=portable_python,
            env=env,
            safe_env=safe_env,
        )
        return
    if prebuilt_exe is not None:
        gui_command = [str(prebuilt_exe)]
        webui_command = [str(prebuilt_exe)]
    elif portable_python is not None:
        gui_command = [str(portable_python), "-m", "gui"]
        webui_command = [
            str(portable_python),
            "main.py",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
    else:
        python = _venv_python(venv)
        gui_command = [str(python), "-m", "gui"]
        webui_command = [
            str(python),
            "main.py",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
    gui = install_dir / "launch-gui.sh"
    gui_safe = install_dir / "launch-gui-safe.sh"
    webui = install_dir / "launch-webui.sh"
    gui.write_text(
        _linux_launcher(app_root, gui_command, env, safe=False),
        encoding="utf-8",
    )
    gui_safe.write_text(
        _linux_launcher(app_root, gui_command, safe_env, safe=True),
        encoding="utf-8",
    )
    webui.write_text(
        _linux_launcher(app_root, webui_command, {}, safe=False),
        encoding="utf-8",
    )
    for path in (gui, gui_safe, webui):
        path.chmod(0o755)


def _write_windows_launchers(
    install_dir: Path,
    app_root: Path,
    venv: Path,
    frontend: str,
    *,
    prebuilt_exe: Path | None = None,
    portable_python: Path | None = None,
    env: dict[str, str] | None = None,
    safe_env: dict[str, str] | None = None,
) -> None:
    env = env or {}
    safe_env = safe_env or {}
    if prebuilt_exe is not None:
        gui_command = f'"{prebuilt_exe}"'
        webui_command = f'"{prebuilt_exe}"'
    elif portable_python is not None:
        gui_command = f'"{portable_python}" -m gui'
        webui_command = (
            f'"{portable_python}" main.py --host 127.0.0.1 --port 8000'
        )
    else:
        python = _venv_python(venv)
        gui_command = f'"{python}" -m gui'
        webui_command = f'"{python}" main.py --host 127.0.0.1 --port 8000'
    (install_dir / "launch-gui.cmd").write_text(
        _windows_launcher(app_root, gui_command, env, safe=False),
        encoding="utf-8",
        newline="",
    )
    (install_dir / "launch-gui-safe.cmd").write_text(
        _windows_launcher(app_root, gui_command, safe_env, safe=True),
        encoding="utf-8",
        newline="",
    )
    (install_dir / "launch-webui.cmd").write_text(
        _windows_launcher(app_root, webui_command, {}, safe=False),
        encoding="utf-8",
        newline="",
    )


def launch_target(install_dir: Path, frontend: str, *, safe: bool = False) -> Path:
    suffix = ".cmd" if IS_WINDOWS else ".sh"
    safe_part = "-safe" if safe else ""
    return install_dir / f"launch-{frontend}{safe_part}{suffix}"


def _desktop_quote(value: str) -> str:
    if any(char in value for char in ' \t"'):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_shortcut_script(
    target: Path,
    working_dir: Path,
    name: str,
    icon: Path | None = None,
    *,
    window_style: int = 1,
) -> str:
    icon_line = ""
    if icon is not None:
        icon_line = f"$s.IconLocation = {_powershell_quote(str(icon))}\n"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$ws = New-Object -ComObject WScript.Shell\n"
        "$dirs = @(\n"
        "  [Environment]::GetFolderPath('Programs'),\n"
        "  [Environment]::GetFolderPath('Desktop')\n"
        ")\n"
        "foreach ($dir in $dirs) {\n"
        "  if (-not $dir) { continue }\n"
        f"  $path = Join-Path $dir {_powershell_quote(name)}\n"
        "  $s = $ws.CreateShortcut($path)\n"
        f"  $s.TargetPath = {_powershell_quote(str(target))}\n"
        f"  $s.WorkingDirectory = {_powershell_quote(str(working_dir))}\n"
        f"  $s.WindowStyle = {int(window_style)}\n"
        f"{icon_line}"
        "  $s.Description = 'KarenBot'\n"
        "  $s.Save()\n"
        "}\n"
    )


def _install_linux_shortcut(
    install_dir: Path,
    frontend: str,
    *,
    prebuilt_exe: Path | None = None,
) -> list[Path]:
    applications = Path.home() / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if frontend == "gui":
        entries = (
            (
                "karenbot-gui.desktop",
                "KarenBot GUI",
                "KarenBot 原生 GTK4 控制台",
                launch_target(install_dir, "gui"),
                False,
            ),
            (
                "karenbot-gui-safe.desktop",
                "KarenBot GUI (兼容模式)",
                "以软件渲染启动 KarenBot GUI（黑屏/崩溃时使用）",
                launch_target(install_dir, "gui", safe=True),
                False,
            ),
        )
    else:
        entries = (
            (
                "karenbot-webui.desktop",
                "KarenBot WebUI",
                "启动 KarenBot WebUI",
                launch_target(install_dir, "webui"),
                True,
            ),
        )
    for filename, name, comment, exec_target, terminal in entries:
        entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={name}\n"
            f"Comment={comment}\n"
            f"Exec={_desktop_quote(str(exec_target))}\n"
            f"Path={install_dir}\n"
            "Icon=applications-system\n"
            f"Terminal={'true' if terminal else 'false'}\n"
            "Categories=Utility;Chat;\n"
            "StartupNotify=true\n"
        )
        path = applications / filename
        path.write_text(entry, encoding="utf-8")
        path.chmod(0o644)
        written.append(path)
    for path in written:
        log(f"桌面快捷方式：{path}")
    return written


def _install_windows_shortcut(
    install_dir: Path,
    frontend: str,
    *,
    prebuilt_exe: Path | None = None,
) -> list[Path]:
    icon = prebuilt_exe if prebuilt_exe is not None else None
    if frontend == "gui":
        entries = (
            (
                "KarenBot GUI.lnk",
                launch_target(install_dir, "gui"),
                7,
            ),
            (
                "KarenBot GUI (兼容模式).lnk",
                launch_target(install_dir, "gui", safe=True),
                7,
            ),
        )
    else:
        entries = (
            (
                "KarenBot WebUI.lnk",
                launch_target(install_dir, "webui"),
                1,
            ),
        )
    written: list[Path] = []
    for name, target, window_style in entries:
        script = _windows_shortcut_script(
            target,
            install_dir,
            name,
            icon,
            window_style=window_style,
        )
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                check=True,
                capture_output=True,
                text=True,
                creationflags=_creation_flags(),
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            log(f"创建 Windows 快捷方式失败（可手动创建）：{exc}")
            continue
        written.append(Path(name))
        log(f"已创建开始菜单/桌面快捷方式：{name}")
    return written


def _install_shortcut(
    install_dir: Path,
    frontend: str,
    *,
    prebuilt_exe: Path | None = None,
) -> Path | None:
    if IS_WINDOWS:
        _install_windows_shortcut(
            install_dir, frontend, prebuilt_exe=prebuilt_exe
        )
        return None
    _install_linux_shortcut(
        install_dir, frontend, prebuilt_exe=prebuilt_exe
    )
    return None


def _write_modules_manifest(
    app_root: Path,
    frontend: str,
    *,
    installer: str = "local-source",
    components: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema": 1,
        "frontend": frontend,
        "components": components or {},
        "installer": installer,
        "installer_version": INSTALLER_VERSION,
    }
    if extra:
        payload.update(extra)
    (app_root / "modules.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 安装流程
# ---------------------------------------------------------------------------


def install(
    *,
    frontend: str,
    install_dir: Path,
    source_dir: Path | None,
    archive: Path | None,
    venv: Path | None,
    skip_deps: bool,
    create_shortcut: bool,
    download_fonts: bool,
    cancel_event: Any | None = None,
    online: bool = False,
    release_tag: str = ONLINE_TAG,
    gui_skin: str = "none",
    render_mode: str = "auto",
    windows_gui_asset: str = "",
    windows_bundle_tag: str = "auto",
) -> Path:
    if gui_skin not in GUI_SKINS:
        fail(f"未知主题包：{gui_skin}")
    if render_mode not in RENDER_MODES:
        fail(f"未知渲染模式：{render_mode}")
    _check_cancel(cancel_event)
    stage("准备安装…")
    install_dir = install_dir.expanduser().resolve()
    _validate_install_path(install_dir, "安装目录")
    ensure_writable_dir(install_dir, "安装目录")
    staging = Path(tempfile.mkdtemp(prefix=".karen-install-", dir=str(install_dir)))
    try:
        installer_kind = "local-source"
        components: dict[str, Any] | None = None
        if source_dir is not None:
            source = source_dir.expanduser().resolve()
            _validate_install_path(source, "源码目录")
            if not source.is_dir():
                fail(f"源码目录不存在：{source}")
            if (
                source == install_dir
                or source in install_dir.parents
                or install_dir in source.parents
            ):
                fail("安装目录和源码目录不能互相包含；请换一个安装目录。")
            if IS_WINDOWS and frontend == "gui":
                fail(
                    "Windows 原生 GUI 不能从源码安装：需要 GTK4/PyGObject 便携运行时。\n"
                    "请选择在线安装（下载预构建包），或使用本地预构建 zip。"
                )
            stage(f"复制源码：{source}")
            shutil.copytree(source, staging / "app", ignore=_IGNORE)
        elif archive is not None:
            installer_kind = "local-archive"
            archive = archive.expanduser().resolve()
            _validate_install_path(archive, "安装包路径")
            if not archive.is_file():
                fail(f"压缩包不存在：{archive}")
            stage(f"解压安装包：{archive}")
            extract_root = staging / "extract"
            extract_archive(archive, extract_root)
            app_root = _find_app_root(extract_root)
            shutil.copytree(app_root, staging / "app", ignore=_IGNORE)
        elif online:
            stage("在线安装：解析 Release 版本…")
            installer_kind, components = _prepare_online_app(
                staging,
                frontend=frontend,
                release_tag=release_tag,
                cancel_event=cancel_event,
                bundle_tag=windows_bundle_tag,
                windows_gui_asset=windows_gui_asset,
            )
        else:
            fail(
                "请提供 --source-dir / --archive，或使用 --online 从 GitHub Release 安装"
            )
        _check_cancel(cancel_event)
        app_root = staging / "app"
        stage("检查安装包类型…")
        portable_python = (
            _portable_python(app_root)
            if frontend == "gui" and IS_WINDOWS
            else None
        )
        prebuilt = _prebuilt_executable(app_root, frontend)
        if portable_python is not None:
            _validate_portable_gui(app_root, portable_python)
            skip_deps = True
            if installer_kind == "local-archive":
                installer_kind = "windows-portable-archive"
            log(
                f"检测到 Windows GTK4 便携运行时：{portable_python}，"
                "跳过 venv/依赖安装"
            )
        elif prebuilt is not None:
            installer_kind = "prebuilt-archive"
            log(f"检测到预构建应用：{prebuilt.name}，跳过 venv/依赖安装")
            skip_deps = True
        else:
            _validate_frontend(app_root, frontend)
            if IS_WINDOWS and frontend == "gui":
                fail(
                    "Windows 原生 GUI 安装包没有 GTK4 便携运行时 "
                    "（runtime/python.exe + GTK4/Adwaita DLL/typelib）。\n"
                    "请使用安装器 Release 的 KarenBot-Windows-x64.zip，"
                    "契约见 docs/windows-gui-package.md。"
                )
        _write_modules_manifest(
            app_root,
            frontend,
            installer=installer_kind,
            components=components,
            extra={
                "gui_skin": gui_skin if frontend == "gui" else "none",
                "render_mode": render_mode if frontend == "gui" else "auto",
            },
        )

        target_app = install_dir / "app"
        old_app = install_dir / "app.old"
        if old_app.exists():
            shutil.rmtree(old_app, ignore_errors=True)
        if target_app.exists():
            target_app.rename(old_app)
        app_root.rename(target_app)

        prebuilt_target: Path | None = None
        if prebuilt is not None:
            prebuilt_target = target_app / prebuilt.name
            if not prebuilt_target.is_file():
                prebuilt_target = _prebuilt_executable(target_app, frontend)
            if prebuilt_target is not None and not IS_WINDOWS:
                # zip 解压通常不保留 Unix 可执行位。
                prebuilt_target.chmod(prebuilt_target.stat().st_mode | 0o755)
        portable_target: Path | None = None
        if portable_python is not None:
            portable_target = _portable_python(target_app)
            if portable_target is not None and not IS_WINDOWS:
                portable_target.chmod(
                    portable_target.stat().st_mode | 0o755
                )
        if portable_target is not None and IS_WINDOWS:
            stage("生成本地图片加载器缓存…")
            _prepare_windows_portable_runtime(target_app, cancel_event)

        target_venv = (
            venv.expanduser().resolve() if venv else install_dir / "venv"
        )
        _check_cancel(cancel_event)
        if not skip_deps:
            base_python = ensure_python_runtime(
                install_dir,
                frontend=frontend,
                cancel_event=cancel_event,
            )
            stage("创建 Python 虚拟环境…")
            _create_venv(
                target_venv,
                frontend=frontend,
                base_python=base_python,
            )
            stage("安装 Python 依赖…")
            _install_dependencies(target_venv, target_app)
            if frontend == "gui":
                _check_gui_runtime(target_venv)
        else:
            log("跳过依赖安装（--skip-deps）")
        if download_fonts:
            stage("下载中文字体…")
            install_fonts(cancel_event=cancel_event)
        if frontend == "gui":
            stage("应用 GUI 主题包…")
            apply_gui_skin(gui_skin)
        _check_cancel(cancel_event)
        stage("写入启动入口…")
        fontconf = _fontconfig_path()
        _write_launchers(
            install_dir,
            target_app,
            target_venv,
            frontend,
            prebuilt_exe=prebuilt_target,
            portable_python=portable_target,
            render_mode=render_mode,
            fontconf=fontconf if fontconf.is_file() else None,
        )
        if create_shortcut:
            stage("创建快捷方式…")
            _install_shortcut(
                install_dir,
                frontend,
                prebuilt_exe=prebuilt_target,
            )
        if old_app.exists():
            shutil.rmtree(old_app, ignore_errors=True)
        stage(f"安装完成：{install_dir}")
        log(f"启动：{launch_target(install_dir, frontend)}")
        if frontend == "gui":
            log(f"兼容模式：{launch_target(install_dir, 'gui', safe=True)}")
        return install_dir
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _prompt(prompt: str, default: str) -> str:
    try:
        value = input(f"{prompt} [{default}]：").strip()
    except EOFError:
        return default
    return value or default


def _prompt_choice(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    print(prompt)
    for key, label in options:
        print(f"  {key}. {label}")
    raw = _prompt("请输入", default)
    valid = {key for key, _label in options}
    return raw if raw in valid else default


def interactive_setup() -> dict[str, Any] | None:
    """NapCat 风格交互菜单；返回覆盖参数，None 表示退出。"""
    print()
    print("KarenBot 安装引导")
    print("  1. 安装原生 GUI（GTK4，推荐桌面用户）")
    print("  2. 安装 WebUI（浏览器控制台）")
    print("  3. 仅测试 GitHub 镜像速度")
    print("  4. 退出")
    choice = _prompt("请选择", "1")
    if choice == "4":
        return None
    if choice == "3":
        url = _prompt(
            "资产 URL",
            "https://github.com/CN-BI5UNF/KarenBot-backend/"
            "archive/refs/tags/v0.1.1-alpha.1.tar.gz",
        )
        return {"probe_only": True, "probe_url": url}
    frontend = "webui" if choice == "2" else "gui"
    install_dir = _prompt("安装目录", str(DEFAULT_INSTALL_DIR))
    download_fonts = _prompt("下载中文字体？(Y/n)", "Y").lower() not in {
        "n",
        "no",
    }
    create_shortcut = _prompt("创建快捷方式？(Y/n)", "Y").lower() not in {
        "n",
        "no",
    }
    gui_skin = "none"
    render_mode = "auto"
    if frontend == "gui":
        if IS_WINDOWS:
            gui_skin = _prompt_choice(
                "选择 Windows GTK4 主题包：",
                [
                    ("1", "Karen 暖色（内置，最接近 Linux 目标机）"),
                    ("2", "系统 Adwaita（GTK4 默认）"),
                    (
                        "3",
                        "不使用美化包（Windows 下观感可能较差，需要自行解决）",
                    ),
                ],
                "1",
            )
            gui_skin = {"1": "karen", "2": "system", "3": "none"}.get(
                gui_skin, "karen"
            )
        render_mode = _prompt_choice(
            "选择渲染模式：",
            [
                ("1", "自动（推荐，失败时自动回退软件渲染）"),
                ("2", "软件渲染（Cairo；黑屏/崩溃时使用）"),
                ("3", "硬件渲染（OpenGL）"),
            ],
            "1",
        )
        render_mode = {"1": "auto", "2": "cairo", "3": "gl"}.get(
            render_mode, "auto"
        )
    return {
        "frontend": frontend,
        "install_dir": install_dir,
        "download_fonts": download_fonts,
        "create_shortcut": create_shortcut,
        "gui_skin": gui_skin,
        "render_mode": render_mode,
    }


def installer_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def self_delete_installer() -> None:
    path = installer_path()
    if IS_WINDOWS and getattr(sys, "frozen", False):
        # Windows 不能删除正在运行的 exe，交给退出后的 cmd 延迟处理。
        command = f'timeout /t 2 /nobreak >nul & del /f /q "{path}"'
        try:
            subprocess.Popen(
                ["cmd", "/c", command],
                creationflags=_creation_flags(),
                close_fds=True,
            )
            log("安装器将在退出后自删除")
        except OSError as exc:
            log(f"安装器自删除安排失败（可手动删除）：{exc}")
        return
    try:
        path.unlink()
        log("安装器已自删除")
    except OSError as exc:
        log(f"安装器自删除失败（可手动删除）：{exc}")


def _print_mirrors() -> None:
    print("可用镜像：")
    for item in load_mirrors():
        kind = str(item.get("kind") or "direct")
        target = str(item.get("prefix") or "GitHub 直连")
        print(f"  {item.get('name')}\t[{kind}] {target}")
        note = str(item.get("note") or "").strip()
        if note:
            print(f"    {note}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="karen-install", description="KarenBot 安装引导器"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {INSTALLER_VERSION}",
    )
    parser.add_argument("--frontend", choices=("gui", "webui"))
    parser.add_argument("--dir", default=str(DEFAULT_INSTALL_DIR), help="安装目录")
    parser.add_argument("--source-dir", default="", help="本地源码目录（测试/离线）")
    parser.add_argument("--archive", default="", help="本地 tar/zip 安装包（测试/离线）")
    parser.add_argument(
        "--online",
        action="store_true",
        help="从 GitHub Release 在线安装（未指定源码/安装包时默认开启）",
    )
    parser.add_argument(
        "--tag",
        default=ONLINE_TAG,
        help=f"在线安装使用的 Release tag（默认 {ONLINE_TAG}）",
    )
    parser.add_argument("--venv", default="", help="复用已有 venv（测试）")
    parser.add_argument("--skip-deps", action="store_true", help="跳过 venv/依赖安装")
    parser.add_argument("--no-shortcut", action="store_true", help="不创建快捷方式")
    parser.add_argument(
        "--no-fonts", action="store_true", help="不下载 Noto 中文字体"
    )
    parser.add_argument(
        "--gui-skin",
        choices=tuple(GUI_SKINS),
        default="karen",
        help="GUI 主题包：karen（目标机同款）、system（Adwaita）、none",
    )
    parser.add_argument(
        "--renderer",
        choices=tuple(RENDER_MODES),
        default="auto",
        help="GUI 渲染模式：auto、cairo（软件）、gl（硬件）",
    )
    parser.add_argument(
        "--mirror",
        default="auto",
        help="固定使用某个镜像（默认 auto 自动测速）；--list-mirrors 查看名称",
    )
    parser.add_argument(
        "--list-mirrors", action="store_true", help="列出内置镜像后退出"
    )
    parser.add_argument(
        "--windows-gui-asset",
        default="",
        help="覆盖 Windows GUI 预构建包的 URL（测试/私有镜像）",
    )
    parser.add_argument(
        "--bundle-tag",
        default="auto",
        help="Windows GUI 便携包所在的安装器 Release tag（默认 auto）",
    )
    parser.add_argument(
        "--self-delete", action="store_true", help="安装完成后删除安装器自身"
    )
    parser.add_argument(
        "--keep-installer", action="store_true", help="安装后保留安装器自身"
    )
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--probe-url", default="", help="只测试镜像速度")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_mirrors:
        _print_mirrors()
        return 0
    set_mirror_override(args.mirror)
    if args.probe_url:
        rank_mirrors(args.probe_url, available_mirrors())
        return 0
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    interactive = (
        not args.non_interactive
        and not raw_args
        and sys.stdin is not None
        and sys.stdin.isatty()
    )
    if interactive:
        setup = interactive_setup()
        if setup is None:
            return 0
        if setup.get("probe_only"):
            rank_mirrors(str(setup["probe_url"]), available_mirrors())
            return 0
        args.frontend = str(setup["frontend"])
        args.dir = str(setup["install_dir"])
        args.no_fonts = not bool(setup["download_fonts"])
        args.no_shortcut = not bool(setup["create_shortcut"])
        args.gui_skin = str(setup["gui_skin"])
        args.renderer = str(setup["render_mode"])
    frontend = args.frontend or "gui"
    if IS_WINDOWS and frontend == "gui" and args.gui_skin == "none":
        log(
            "提示：未选择美化包。Windows 下 GTK4 默认外观可能不如 Linux，"
            "需要自行解决；安装器不提供额外方案。"
        )
    install_dir = Path(args.dir).expanduser()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "frontend": frontend,
                    "install_dir": str(install_dir),
                    "source_dir": args.source_dir,
                    "archive": args.archive,
                    "venv": args.venv,
                    "skip_deps": args.skip_deps,
                    "download_fonts": not args.no_fonts,
                    "create_shortcut": not args.no_shortcut,
                    "gui_skin": args.gui_skin,
                    "render_mode": args.renderer,
                    "mirror": args.mirror,
                    "self_delete": args.self_delete,
                    "online": not (args.source_dir or args.archive),
                    "tag": args.tag,
                    "bundle_tag": args.bundle_tag,
                    "windows_gui_asset": args.windows_gui_asset,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    online = not (args.source_dir or args.archive)
    install(
        frontend=frontend,
        install_dir=install_dir,
        source_dir=Path(args.source_dir) if args.source_dir else None,
        archive=Path(args.archive) if args.archive else None,
        venv=Path(args.venv) if args.venv else None,
        skip_deps=args.skip_deps,
        create_shortcut=not args.no_shortcut,
        download_fonts=not args.no_fonts,
        online=online,
        release_tag=args.tag,
        gui_skin=args.gui_skin,
        render_mode=args.renderer,
        windows_gui_asset=args.windows_gui_asset,
        windows_bundle_tag=args.bundle_tag,
    )
    should_delete = args.self_delete or (
        getattr(sys, "frozen", False) and not args.keep_installer
    )
    if should_delete:
        self_delete_installer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
