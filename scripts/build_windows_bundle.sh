#!/usr/bin/env bash
# 在 GitHub Actions 的 MSYS2 UCRT64 环境里组装 Windows GTK4 便携包。
# 产物：KarenBot-Windows-x64.zip，包含 backend/ + gui/ + runtime/。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="${BACKEND_DIR:-backend}"
GUI="${GUI_DIR:-gui-src}"
case "$BACKEND" in
    /* | [A-Za-z]:*) ;;
    *) BACKEND="$ROOT/$BACKEND" ;;
esac
case "$GUI" in
    /* | [A-Za-z]:*) ;;
    *) GUI="$ROOT/$GUI" ;;
esac
PREFIX="${MSYSTEM_PREFIX:-/ucrt64}"
BUNDLE_ROOT="$ROOT/windows-bundle"
BUNDLE="$BUNDLE_ROOT/KarenBot-Windows-x64"
RUNTIME="$BUNDLE/runtime"
ARCHIVE="$ROOT/KarenBot-Windows-x64.zip"
BACKEND_TAG="${BACKEND_TAG:-unknown}"
GUI_TAG="${GUI_TAG:-unknown}"

for required in "$BACKEND/backend" "$GUI/gui"; do
    if [ ! -d "$required" ]; then
        echo "缺少目录：$required" >&2
        exit 1
    fi
done

PYTHON="$(command -v python)"
PYVER="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "== MSYS2 prefix: $PREFIX"
echo "== Python: $PYTHON ($PYVER)"
echo "== Backend: $BACKEND ($BACKEND_TAG)"
echo "== GUI: $GUI ($GUI_TAG)"

rm -rf "$BUNDLE_ROOT" "$ARCHIVE"
mkdir -p "$BUNDLE" "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/share" "$RUNTIME/etc"

echo "== Install backend dependencies into MSYS2 Python"
python -m pip install --upgrade pip
python -m pip install "$(cygpath -w "$BACKEND")"

echo "== Copy application sources"
cp -a "$BACKEND"/. "$BUNDLE"/
rm -rf \
    "$BUNDLE/.git" "$BUNDLE/.venv" "$BUNDLE/data" "$BUNDLE/tests" \
    "$BUNDLE/docs" "$BUNDLE/build" "$BUNDLE/dist" "$BUNDLE/release" \
    "$BUNDLE/module_backups" "$BUNDLE/backups" "$BUNDLE/.pytest_cache" \
    "$BUNDLE/.ruff_cache"
find "$BUNDLE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUNDLE" -name '*.pyc' -delete 2>/dev/null || true
find "$BUNDLE" -maxdepth 1 -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$BUNDLE/gui"
cp -a "$GUI/gui" "$BUNDLE/gui"

echo "== Copy Python runtime"
cp "$PREFIX/bin/python.exe" "$PREFIX/bin/pythonw.exe" "$RUNTIME/bin/"
cp -a "$PREFIX/lib/python$PYVER" "$RUNTIME/lib/"
cp -a "$PREFIX/lib/girepository-1.0" "$RUNTIME/lib/"
if [ -d "$PREFIX/lib/gdk-pixbuf-2.0" ]; then
    cp -a "$PREFIX/lib/gdk-pixbuf-2.0" "$RUNTIME/lib/"
fi
if [ -d "$PREFIX/lib/gio" ]; then
    cp -a "$PREFIX/lib/gio" "$RUNTIME/lib/"
fi

echo "== Copy GTK resources"
for resource in glib-2.0 icons themes gtk-4.0 fontconfig; do
    if [ -d "$PREFIX/share/$resource" ]; then
        cp -a "$PREFIX/share/$resource" "$RUNTIME/share/"
    fi
done
if [ -d "$PREFIX/etc/fonts" ]; then
    cp -a "$PREFIX/etc/fonts" "$RUNTIME/etc/"
fi
if command -v glib-compile-schemas >/dev/null 2>&1; then
    glib-compile-schemas "$RUNTIME/share/glib-2.0/schemas"
fi

echo "== Collect DLL dependency closure"
export PATH="$PREFIX/bin:$PATH"
declare -A SEEN=()
copy_closure() {
    local -a queue=("$@")
    while ((${#queue[@]})); do
        local item="${queue[0]}"
        queue=("${queue[@]:1}")
        [ -f "$item" ] || continue
        case "$item" in
            "$PREFIX/bin/"*)
                local root_base
                root_base="$(basename "$item")"
                if [ -z "${SEEN[$root_base]:-}" ]; then
                    SEEN[$root_base]=1
                    cp -f "$item" "$RUNTIME/bin/$root_base"
                fi
                ;;
        esac
        while IFS= read -r dep; do
            [ -n "$dep" ] || continue
            case "$dep" in
                [A-Za-z]:*) dep="$(cygpath -u "$dep" 2>/dev/null || printf '%s' "$dep")" ;;
            esac
            case "$dep" in
                "$PREFIX"/* | /ucrt64/*) ;;
                *) continue ;;
            esac
            local base
            base="$(basename "$dep")"
            if [ -z "${SEEN[$base]:-}" ]; then
                SEEN[$base]=1
                cp -f "$dep" "$RUNTIME/bin/$base"
                queue+=("$dep")
            fi
        done < <(
            ldd "$item" 2>/dev/null |
                awk -F'=> ' '/=>/ {print $2}' |
                awk '{print $1}'
        )
    done
}

roots=(
    "$PREFIX/bin/python.exe"
    "$PREFIX/bin/pythonw.exe"
    "$PREFIX/bin/libgtk-4-1.dll"
    "$PREFIX/bin/libadwaita-1-0.dll"
    "$PREFIX/bin/libgdk_pixbuf-2.0-0.dll"
)
while IFS= read -r dll; do
    roots+=("$dll")
done < <(find "$PREFIX/bin" -maxdepth 1 -iname 'libgirepository*.dll' 2>/dev/null || true)
while IFS= read -r dll; do
    roots+=("$dll")
done < <(find "$PREFIX/lib/gdk-pixbuf-2.0" -name '*.dll' 2>/dev/null || true)
while IFS= read -r dll; do
    roots+=("$dll")
done < <(find "$PREFIX/lib/gio" -name '*.dll' 2>/dev/null || true)
GI_PYD="$(python -c 'import gi; print(gi._gi.__file__)' 2>/dev/null || true)"
if [ -n "$GI_PYD" ]; then
    roots+=("$(cygpath -u "$GI_PYD")")
fi
if [ -f "$PREFIX/bin/gdk-pixbuf-query-loaders.exe" ]; then
    roots+=("$PREFIX/bin/gdk-pixbuf-query-loaders.exe")
    cp "$PREFIX/bin/gdk-pixbuf-query-loaders.exe" "$RUNTIME/bin/"
fi
copy_closure "${roots[@]}"

echo "== Write bundle manifest"
GTK_VERSION="$(
    python - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
print(f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")
PY
)"
PY_VERSION="$(python -c 'import platform; print(platform.python_version())')"
python - "$BUNDLE/windows-bundle.json" "$BACKEND_TAG" "$GUI_TAG" \
    "$GTK_VERSION" "$PY_VERSION" <<'PY'
import json
import sys

path, backend_tag, gui_tag, gtk_version, python_version = sys.argv[1:]
payload = {
    "schema": 1,
    "backend": {
        "repo": "CN-BI5UNF/KarenBot-backend",
        "tag": backend_tag,
    },
    "gui": {
        "repo": "CN-BI5UNF/KarenBot-GUI",
        "tag": gui_tag,
    },
    "runtime": {
        "kind": "windows-gtk4-portable",
        "python": python_version,
        "gtk": gtk_version,
    },
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY

echo "== Smoke test bundled runtime"
export XDG_DATA_DIRS="$RUNTIME/share"
export GI_TYPELIB_PATH="$RUNTIME/lib/girepository-1.0"
export GSETTINGS_SCHEMA_DIR="$RUNTIME/share/glib-2.0/schemas"
export GTK_DATA_PREFIX="$RUNTIME"
export GTK_EXE_PREFIX="$RUNTIME"
export GDK_PIXBUF_MODULEDIR="$RUNTIME/lib/gdk-pixbuf-2.0/2.10.0/loaders"
export GDK_PIXBUF_MODULE_FILE="$RUNTIME/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache"
if [ -f "$RUNTIME/etc/fonts/fonts.conf" ]; then
    export FONTCONFIG_FILE="$RUNTIME/etc/fonts/fonts.conf"
fi
"$RUNTIME/bin/python.exe" -c \
    "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw; print('GTK', Gtk.get_major_version(), Gtk.get_minor_version(), 'Adw', Adw.get_major_version())"
"$RUNTIME/bin/python.exe" -c \
    "import fastapi, httpx, pydantic; print('backend deps ok')"
(
    cd "$BUNDLE"
    "$RUNTIME/bin/python.exe" -m gui --version
)

echo "== Create archive"
(
    cd "$BUNDLE_ROOT"
    if command -v 7z >/dev/null 2>&1; then
        7z a -tzip -mx=9 "$(cygpath -w "$ARCHIVE")" "KarenBot-Windows-x64"
    else
        zip -r -q -9 "$ARCHIVE" "KarenBot-Windows-x64"
    fi
)
ls -lh "$ARCHIVE"
du -sh "$BUNDLE" "$RUNTIME"
