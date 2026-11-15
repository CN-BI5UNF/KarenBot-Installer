#!/usr/bin/env bash
# KarenBot 安装引导器（NapCat 风格的一行式启动脚本）。
#
# 用法：
#   bash <(curl -fsSL https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/main/install.sh)
#   ./install.sh --frontend gui --online
#
# 脚本本身只负责找到 karen_install.py / 冻结版二进制并转交参数；
# 测速、下载、校验和安装都由 Python 安装核心完成。不构建 deb 等系统包。
set -euo pipefail

REPO="${KAREN_INSTALLER_REPO:-CN-BI5UNF/KarenBot-Installer}"
REF="${KAREN_INSTALLER_REF:-main}"
PYTHON="${KAREN_INSTALL_PYTHON:-python3}"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || pwd)"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "需要 Python 3.10+；请先安装 python3（Debian/Ubuntu: sudo apt install python3）。" >&2
    exit 1
fi

# 支持 curl | bash：脚本从管道读取时，只把安装器的标准输入接回终端。
run_installer() {
    if [ ! -t 0 ] && (exec </dev/tty) 2>/dev/null; then
        "$@" </dev/tty
    else
        "$@"
    fi
}

# 仓库内直接运行：优先使用同目录的 Python 源码。
if [ -f "$HERE/karen_install.py" ]; then
    run_installer "$PYTHON" "$HERE/karen_install.py" "$@"
    exit $?
fi

case "$(uname -m 2>/dev/null || true)" in
    x86_64 | amd64) ARCH="x64" ;;
    aarch64 | arm64) ARCH="arm64" ;;
    *) ARCH="" ;;
esac
# 冻结版是 GUI 入口（只接受 --check/--smoke-test）；带 CLI 参数时继续用
# Python 安装核心，避免把 --frontend 等参数传给 GUI。
if [ -n "$ARCH" ] && [ -x "$HERE/KarenBot-Installer-linux-$ARCH" ] && [ "$#" -eq 0 ]; then
    run_installer "$HERE/KarenBot-Installer-linux-$ARCH"
    exit $?
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "需要 curl；请先安装 curl。" >&2
    exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/karen-installer.XXXXXX")"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

BASE_OVERRIDE="${KAREN_INSTALLER_BASE:-}"
if [ -n "$BASE_OVERRIDE" ]; then
    BASES=("${BASE_OVERRIDE%/}")
else
    RAW="https://raw.githubusercontent.com/$REPO/$REF"
    BASES=(
        "$RAW"
        "https://cdn.jsdelivr.net/gh/$REPO@$REF"
        "https://fastly.jsdelivr.net/gh/$REPO@$REF"
        "https://ghproxy.net/https://raw.githubusercontent.com/$REPO/$REF"
        "https://gh-proxy.com/https://raw.githubusercontent.com/$REPO/$REF"
        "https://raw.gitmirror.com/$REPO/$REF"
    )
fi

BASE=""
for candidate in "${BASES[@]}"; do
    echo "获取安装核心：$candidate/karen_install.py"
    if curl -fsSL --retry 2 --retry-delay 1 --connect-timeout 8 --max-time 120 \
        "$candidate/karen_install.py" -o "$TMP_DIR/karen_install.py"; then
        BASE="$candidate"
        break
    fi
done

if [ -z "$BASE" ]; then
    echo "无法获取 karen_install.py；请检查网络，或用 KAREN_INSTALLER_BASE 指定可访问的镜像。" >&2
    exit 1
fi

if [ -n "${KAREN_INSTALLER_SHA256:-}" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
        ACTUAL="$(sha256sum "$TMP_DIR/karen_install.py" | awk '{print $1}')"
    else
        ACTUAL="$(shasum -a 256 "$TMP_DIR/karen_install.py" | awk '{print $1}')"
    fi
    if [ "$ACTUAL" != "$KAREN_INSTALLER_SHA256" ]; then
        echo "karen_install.py SHA256 校验失败：$ACTUAL" >&2
        exit 1
    fi
fi

# 资源文件缺失时 Python 核心有内置默认值；能拿到就一起放在临时目录。
for resource in mirrors.json windows-gui-package.json; do
    curl -fsSL --retry 1 --connect-timeout 8 --max-time 60 \
        "$BASE/$resource" -o "$TMP_DIR/$resource" 2>/dev/null || true
done

set +e
run_installer "$PYTHON" "$TMP_DIR/karen_install.py" "$@"
STATUS=$?
set -e
exit "$STATUS"
