# Windows GTK4 GUI 预构建包契约

KarenBot 安装器不在 Windows 上从源码安装原生 GUI。GTK4 + libadwaita +
PyGObject 在 Windows 上需要一整套便携运行时，因此由 **KarenBot-GUI 仓库的
Windows 构建流水线**产出一个 zip，安装器负责下载、校验和安装。

资产名固定为：

```text
KarenBot-GUI-Windows-x64.zip
```

发布位置为 GUI 仓库 Release：

```text
https://github.com/CN-BI5UNF/KarenBot-GUI/releases/download/<tag>/KarenBot-GUI-Windows-x64.zip
```

安装器也接受 `KAREN_GUI_WINDOWS_ASSET=<本地路径或 URL>` /
`--windows-gui-asset` 覆盖，方便测试和私有镜像。

## 目录契约

zip 内可以有一个顶层目录（例如 `KarenBot-GUI-Windows-x64/`），安装器会自动
定位应用根目录。应用根目录必须满足：

```text
backend/                         # KarenBot 共享后端
gui/                             # KarenBot-GUI 模块（含 plugin.json、launcher.py）
main.py                          # KarenBot 宿主入口
runtime/
  python.exe                     # 或 runtime/bin/python.exe
  pythonw.exe
  bin/
    libgtk-4-1.dll
    libadwaita-1-0.dll
    libgobject-2.0-0.dll
    ...                          # GTK/Pango/GDK/GLib 依赖闭包
  lib/
    girepository-1.0/
      Gtk-4.0.typelib
      Adw-1.typelib
      Gdk-4.0.typelib
      ...
  share/
    glib-2.0/schemas/
    icons/
    themes/
```

安装器按 `windows-gui-package.json` 检查：

- `required_dirs`: `backend`, `gui`；
- `python_candidates`: `runtime/python.exe` 等；
- `required_dlls`: `libgtk-4-1.dll`, `libadwaita-1-0.dll`,
  `libgobject-2.0-0.dll`；
- `typelib_dirs` + `required_typelibs`: `Gtk-4.0.typelib`, `Adw-1.typelib`。

缺任何一项都会直接失败，并提示按本契约重新打包，避免安装后黑屏/缺字。

## 推荐工具链（API 用户选择，安装器不构建）

建议在 GitHub Actions 的 `windows-2022` runner 上用 **MSYS2 UCRT64**：

```text
mingw-w64-ucrt-x86_64-python
mingw-w64-ucrt-x86_64-python-gobject
mingw-w64-ucrt-x86_64-gtk4
mingw-w64-ucrt-x86_64-libadwaita
mingw-w64-ucrt-x86_64-gdk-pixbuf2
mingw-w64-ucrt-x86_64-librsvg
mingw-w64-ucrt-x86_64-adwaita-icon-theme
mingw-w64-ucrt-x86_64-hicolor-icon-theme
```

版本必须固定（建议在 GUI 仓库提交 `msys2-packages.lock` 或在 workflow 中
写死包版本），不要依赖 `pacman -Syu` 的最新状态。打包时：

1. 用 `ntldd` 或等价方式收集 `ucrt64/bin` 的 DLL 依赖闭包；
2. 复制 GTK/Pango/GDK-Pixbuf/GLib 的 `share/glib-2.0/schemas`、`share/icons`、
   `share/themes`、`share/fontconfig`（如使用）；
3. 复制 `lib/girepository-1.0` 的 typelib；
4. 把 `runtime/` 的目录布局固定为上面的契约；
5. 用 `runtime/python.exe -m gui --version` / `--check` 做冒烟测试；
6. 产出 zip 并在 Release 上传。

安装器会在启动时设置：

```text
PATH=<app>\runtime\bin;%PATH%
XDG_DATA_DIRS=<app>\runtime\share
GI_TYPELIB_PATH=<app>\runtime\lib\girepository-1.0
GSETTINGS_SCHEMA_DIR=<app>\runtime\share\glib-2.0\schemas
GTK_DATA_PREFIX=<app>\runtime
GTK_EXE_PREFIX=<app>\runtime
FONTCONFIG_FILE=%LOCALAPPDATA%\KarenBot\fonts\fonts.conf
```

## 主题与字体

- 主题包由 GUI 内置：`karen`（目标机同款）和 `system`（Adwaita）。安装器
  写入 `%APPDATA%\karen-gui\skin` 和 `%LOCALAPPDATA%\karen-gui\skin`；
- 不内置第三方 GTK4 主题资产，避免许可证和资源路径问题；
- 字体由安装器下载 Noto Sans SC / Noto Serif SC 到
  `%LOCALAPPDATA%\KarenBot\fonts`，并生成 `fonts.conf`；
- GUI 的 `gui/style.css` 已优先使用 `Noto Sans SC` / `Noto Serif SC`，不需要
  修改应用源码。

## 渲染回退

`gui/launcher.py` 在导入 `gi` 前按 stage 回退。安装器的
`launch-gui.cmd` / `launch-gui-safe.cmd` 只设置环境变量，不传 `--renderer`
参数：

```text
KAREN_GUI_RENDERER=cairo
GSK_RENDERER=cairo
GDK_DEBUG=gl-disable
LIBGL_ALWAYS_SOFTWARE=1
GDK_BACKEND=win32          # 仅兼容模式
```

如果 GUI 仓库后续增加新的启动器入口，请保持“环境变量在导入 gi 之前生效”
这一约束，否则回退不会起作用。
