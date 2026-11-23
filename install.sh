#!/usr/bin/env bash
# KarenBot 安装引导器（NapCat 风格的一行式启动脚本）。
#
# 用法：
#   bash <(curl -fsSL https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/main/install.sh)
#   ./install.sh --frontend gui --online
#
# raw.githubusercontent.com 被重置时，请使用 README 里的多源一行式命令，
# 或先通过镜像下载本脚本后执行：bash install.sh [参数]。
#
# 脚本本身只负责找到 karen_install.py / 冻结版二进制并转交参数；
# 测速、下载、校验和安装都由 Python 安装核心完成。不构建 deb 等系统包。
set -euo pipefail

REPO="${KAREN_INSTALLER_REPO:-CN-BI5UNF/KarenBot-Installer}"
REF="${KAREN_INSTALLER_REF:-main}"
PYTHON="${KAREN_INSTALL_PYTHON:-python3}"
HERE=""
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
if [ -n "$SCRIPT_SOURCE" ] && [ -f "$SCRIPT_SOURCE" ]; then
    HERE="$(cd -- "$(dirname -- "$SCRIPT_SOURCE")" 2>/dev/null && pwd || true)"
fi

is_direct_base() {
    case "$1" in
        https://raw.githubusercontent.com/*) return 0 ;;
        *) return 1 ;;
    esac
}

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
if [ -n "$HERE" ] && [ -f "$HERE/karen_install.py" ]; then
    run_installer "$PYTHON" "$HERE/karen_install.py" "$@"
    exit $?
fi

case "$(uname -m 2>/dev/null || true)" in
    x86_64 | amd64) ARCH="x64" ;;
    aarch64 | arm64) ARCH="arm64" ;;
    *) ARCH="" ;;
esac
# 冻结版是 GUI 入口（可直接运行，也接受 --check/--smoke-test/--self-test/
# --uninstall 等安装器参数）；带安装参数时继续用 Python 安装核心，避免把
# --frontend 等参数传给 GUI。
if [ -n "$HERE" ] && [ -n "$ARCH" ] && \
    [ -x "$HERE/KarenBot-Installer-linux-$ARCH" ] && [ "$#" -eq 0 ]; then
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
        "https://gh-proxy.com/https://raw.githubusercontent.com/$REPO/$REF"
        "https://ghproxy.net/https://raw.githubusercontent.com/$REPO/$REF"
        "https://fastly.jsdelivr.net/gh/$REPO@$REF"
        "https://ghproxy.cfd/https://raw.githubusercontent.com/$REPO/$REF"
        "https://cdn.jsdelivr.net/gh/$REPO@$REF"
    )
fi

BASE=""
EXPECTED_SHA256="$(printf '%s' "${KAREN_INSTALLER_SHA256:-}" | tr '[:upper:]' '[:lower:]')"
if [ -n "$EXPECTED_SHA256" ] && [[ ! "$EXPECTED_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "KAREN_INSTALLER_SHA256 必须是 64 位十六进制 SHA256。" >&2
    exit 1
fi
for candidate in "${BASES[@]}"; do
    echo "获取安装核心：$candidate/karen_install.py"
    if curl -fsSL --retry 2 --retry-delay 1 --connect-timeout 8 --max-time 30 \
        "$candidate/karen_install.py" -o "$TMP_DIR/karen_install.py"; then
        if [ -n "$EXPECTED_SHA256" ]; then
            if command -v sha256sum >/dev/null 2>&1; then
                ACTUAL="$(sha256sum "$TMP_DIR/karen_install.py" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')"
            elif command -v shasum >/dev/null 2>&1; then
                ACTUAL="$(shasum -a 256 "$TMP_DIR/karen_install.py" | awk '{print $1}' | tr '[:upper:]' '[:lower:]')"
            else
                echo "需要 sha256sum 或 shasum 才能校验 KAREN_INSTALLER_SHA256。" >&2
                exit 1
            fi
            if [ "$ACTUAL" != "$EXPECTED_SHA256" ]; then
                echo "karen_install.py SHA256 校验失败：$ACTUAL" >&2
                rm -f "$TMP_DIR/karen_install.py"
                continue
            fi
        elif ! is_direct_base "$candidate"; then
            echo "拒绝从第三方镜像运行未经校验的 karen_install.py；请设置 KAREN_INSTALLER_SHA256。" >&2
            exit 1
        fi
        BASE="$candidate"
        break
    fi
done

if [ -z "$BASE" ]; then
    echo "无法获取 karen_install.py；请检查网络，或用 KAREN_INSTALLER_BASE 指定可访问的镜像。" >&2
    exit 1
fi

# 资源文件缺失时 Python 核心有内置默认值；能拿到就一起放在临时目录。
if is_direct_base "$BASE"; then
    for resource in mirrors.json windows-gui-package.json packaging/karenbot.svg; do
        name="${resource##*/}"
        curl -fsSL --retry 1 --connect-timeout 8 --max-time 60 \
            "$BASE/$resource" -o "$TMP_DIR/$name" 2>/dev/null || true
    done
fi

set +e
run_installer "$PYTHON" "$TMP_DIR/karen_install.py" "$@"
STATUS=$?
set -e
exit "$STATUS"
