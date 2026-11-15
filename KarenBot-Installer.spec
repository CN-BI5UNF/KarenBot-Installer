# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：Tkinter 安装器单文件。

Windows 使用 windowed 子系统（无黑框），并写入文件版本信息；Linux 保留
console，方便 `--check` / `--smoke-test` 和终端启动时看到错误。

体积优化：剔除安装器不使用的 decimal/unittest/pdb，以及 Tcl 的时区库、
tcltest 和无关的消息表。安装器只使用 Tkinter 基础控件，这些数据对功能
没有影响，但会拖慢单文件冷启动解压。

Tcl 的 encoding 表全部保留：Windows 的 ANSI 代码页因系统区域设置而异，
删掉非中文编码会让部分语言的系统上 Tk 初始化失败。
"""

import sys
from pathlib import Path

INSTALLER_DIR = Path(SPECPATH).resolve()
VERSION_FILE = INSTALLER_DIR / "packaging" / "windows-version-info.txt"
MANIFEST_FILE = INSTALLER_DIR / "packaging" / "windows-manifest.xml"
ICON_FILE = INSTALLER_DIR / "packaging" / "karenbot.ico"

DATAS = [
    (str(INSTALLER_DIR / "mirrors.json"), "."),
    (str(INSTALLER_DIR / "windows-gui-package.json"), "."),
]
if ICON_FILE.is_file():
    DATAS.append((str(ICON_FILE), "."))

a = Analysis(
    [str(INSTALLER_DIR / "gui_install.py")],
    pathex=[str(INSTALLER_DIR)],
    binaries=[],
    datas=DATAS,
    hiddenimports=["karen_install"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 注意：不能排除 multiprocessing —— concurrent.futures 包的 __init__
        # 会无条件导入 ProcessPoolExecutor，冻结后会 ModuleNotFoundError。
        "decimal",
        "unittest",
        "pdb",
        "_wmi",
        "_decimal",
    ],
    noarchive=False,
)


def _keep_tcl_data(entry: tuple[str, str, str]) -> bool:
    """只保留安装器实际用得到的 Tcl 数据。"""
    dest, src = entry[0], entry[1]
    text = f"{dest}/{src}".replace("\\", "/").lower()
    if "/tzdata/" in text or "/tcltest/" in text:
        return False
    if "/_tcl_data/msgs/" in text:
        name = text.rsplit("/", 1)[-1]
        return name.startswith(("zh", "en"))
    return True


a.datas = [entry for entry in a.datas if _keep_tcl_data(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KarenBot-Installer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=not sys.platform.startswith("win"),
    icon=(
        str(ICON_FILE)
        if sys.platform.startswith("win") and ICON_FILE.is_file()
        else None
    ),
    version=(
        str(VERSION_FILE)
        if sys.platform.startswith("win") and VERSION_FILE.is_file()
        else None
    ),
    manifest=(
        str(MANIFEST_FILE)
        if sys.platform.startswith("win") and MANIFEST_FILE.is_file()
        else None
    ),
)
