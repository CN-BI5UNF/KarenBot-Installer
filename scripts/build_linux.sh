#!/usr/bin/env bash
# 在 Linux 构建机上打包单文件安装器；CI 在 Ubuntu 22.04 容器内调用。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

cd "$ROOT"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements-build.txt
"$PYTHON" -m PyInstaller KarenBot-Installer.spec --noconfirm --clean --log-level INFO
