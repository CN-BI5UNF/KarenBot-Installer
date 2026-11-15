# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：Tkinter 安装器单文件。

Windows 使用 windowed 子系统（无黑框），并写入文件版本信息；Linux 保留
console，方便 `--check` / `--smoke-test` 和终端启动时看到错误。
"""

import sys
from pathlib import Path

INSTALLER_DIR = Path(SPECPATH).resolve()
VERSION_FILE = INSTALLER_DIR / "packaging" / "windows-version-info.txt"
VENDOR_DIR = INSTALLER_DIR / "vendor"
BUNDLED_PYTHON = VENDOR_DIR / "python-runtime.tar.gz"
BUNDLED_HASH = VENDOR_DIR / "python-runtime.sha256"

DATAS = [
    (str(INSTALLER_DIR / "mirrors.json"), "."),
    (str(INSTALLER_DIR / "windows-gui-package.json"), "."),
]
for _vendor_file in (BUNDLED_PYTHON, BUNDLED_HASH):
    if _vendor_file.is_file():
        DATAS.append((str(_vendor_file), "."))

a = Analysis(
    [str(INSTALLER_DIR / "gui_install.py")],
    pathex=[str(INSTALLER_DIR)],
    binaries=[],
    datas=DATAS,
    hiddenimports=["karen_install"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

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
    version=(
        str(VERSION_FILE)
        if sys.platform.startswith("win") and VERSION_FILE.is_file()
        else None
    ),
)
