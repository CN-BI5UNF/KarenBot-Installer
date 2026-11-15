# Windows GTK4 GUI 便携包契约

KarenBot 安装器不在 Windows 上从源码安装原生 GUI。GTK4 + libadwaita +
PyGObject 在 Windows 上需要一整套便携运行时，因此由 **本仓库的 GitHub
Actions（`build-windows-bundle` job）** 用 MSYS2 UCRT64 组装，附加到安装器
自己的 Release；安装器只负责下载、校验和安装。GUI 仓库不需要发布任何资产。

资产名固定为：

```text
KarenBot-Windows-x64.zip
```

发布位置为安装器 Release：

```text
https://github.com/CN-BI5UNF/KarenBot-Installer/releases/download/<installer-tag>/KarenBot-Windows-x64.zip
```

安装器默认解析安装器仓库的最新 Release，也可以用 `--bundle-tag` 指定；
`KAREN_GUI_WINDOWS_ASSET=<本地路径或 URL>` / `--windows-gui-asset` 可覆盖，
方便测试和私有镜像。

## 目录契约

zip 内有一个顶层目录：

```text
KarenBot-Windows-x64/
  backend/                         # KarenBot-backend
  gui/                             # KarenBot-GUI 模块（含 plugin.json、launcher.py）
  main.py                          # KarenBot 宿主入口
  windows-bundle.json              # 本次打包的 backend/GUI/Python/GTK 版本
  runtime/
    bin/
      python.exe
      pythonw.exe
      libgtk-4-1.dll
      libadwaita-1-0.dll
      libgobject-2.0-0.dll
      gdk-pixbuf-query-loaders.exe
      ...                          # 依赖 DLL 闭包
    lib/
      python3.XX/                  # 标准库 + site-packages（pip、gi、后端依赖）
      girepository-1.0/
        Gtk-4.0.typelib
        Adw-1.typelib
        ...
      gdk-pixbuf-2.0/2.10.0/loaders/
      gio/modules/
    share/
      glib-2.0/schemas/            # 含编译后的 gschemas.compiled
      icons/
      themes/
      gtk-4.0/
    etc/fonts/                     # 可选的 fontconfig 配置
```

安装器按 `windows-gui-package.json` 检查：

- `required_dirs`: `backend`, `gui`；
- `python_candidates`: `runtime/python.exe` 等；
- `required_dlls`: `libgtk-4-1.dll`, `libadwaita-1-0.dll`,
  `libgobject-2.0-0.dll`；
- `typelib_dirs` + `required_typelibs`: `Gtk-4.0.typelib`, `Adw-1.typelib`。

缺任何一项都会直接失败，并提示按本契约重新打包，避免安装后黑屏/缺字。

## 构建工具链

`.github/workflows/build-installer.yml` 的 `build-windows-bundle` job：

1. checkout 本仓库、`KarenBot-backend`、`KarenBot-GUI`（tag 可手动指定）；
2. `msys2/setup-msys2@v2` 安装 UCRT64 工具链：

```text
mingw-w64-ucrt-x86_64-python
mingw-w64-ucrt-x86_64-python-pip
mingw-w64-ucrt-x86_64-python-gobject
mingw-w64-ucrt-x86_64-gtk4
mingw-w64-ucrt-x86_64-libadwaita
mingw-w64-ucrt-x86_64-gdk-pixbuf2
mingw-w64-ucrt-x86_64-librsvg
mingw-w64-ucrt-x86_64-adwaita-icon-theme
mingw-w64-ucrt-x86_64-hicolor-icon-theme
```

3. 运行 `scripts/build_windows_bundle.sh`：
   - 用 MSYS2 Python 安装 backend 依赖；
   - 复制 `backend/` + `gui/`；
   - 复制 Python 标准库、site-packages、GTK/Adwaita DLL 与依赖闭包；
   - 复制 typelib、Gio 模块、gdk-pixbuf loaders、icons/themes/schemas；
   - 写 `windows-bundle.json`；
   - 在打包环境里做 `import gi`、backend 依赖和 `python -m gui --version` 冒烟；
   - 生成 `KarenBot-Windows-x64.zip`。
4. 上传 artifact；推送 `v*` tag 时由 `release` job 附加到安装器 Release。

安装器安装后会按最终安装路径重新生成
`runtime/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache`，避免打包机绝对路径失效。

## 运行时环境

安装器生成的 `launch-gui.cmd` / `launch-gui-safe.cmd` 会设置：

```text
PATH=<app>\runtime\bin;%PATH%
XDG_DATA_DIRS=<app>\runtime\share
GI_TYPELIB_PATH=<app>\runtime\lib\girepository-1.0
GSETTINGS_SCHEMA_DIR=<app>\runtime\share\glib-2.0\schemas
GTK_DATA_PREFIX=<app>\runtime
GTK_EXE_PREFIX=<app>\runtime
GDK_PIXBUF_MODULEDIR=<app>\runtime\lib\gdk-pixbuf-2.0\2.10.0\loaders
GDK_PIXBUF_MODULE_FILE=<app>\runtime\lib\gdk-pixbuf-2.0\2.10.0\loaders.cache
GIO_MODULE_DIR=<app>\runtime\lib\gio\modules
FONTCONFIG_FILE=%LOCALAPPDATA%\KarenBot\fonts\fonts.conf
```

## 主题、字体与渲染

- 主题包由 GUI 内置：`karen`（目标机同款）和 `system`（Adwaita）。安装器
  写入 `%APPDATA%\karen-gui\skin` 和 `%LOCALAPPDATA%\karen-gui\skin`；
- 不内置第三方 GTK4 主题资产，避免许可证和资源路径问题；
- 字体由安装器下载 Noto Sans SC / Noto Serif SC 到
  `%LOCALAPPDATA%\KarenBot\fonts`，并生成 `fonts.conf`；
- `gui/launcher.py` 在导入 `gi` 前按 stage 回退；安装器的
  `launch-gui-safe.cmd` 从 Cairo 软件渲染开始。

## 安装器自身大小

Windows 安装器内置一个 **stripped Python 3.12.16**（约 22 MB），解压到
`<安装目录>/python-runtime`，用于没有完整全局 Python 的用户；安装器 exe
成品仍要求 ≤ 50 MiB。GTK4 大运行时不在 exe 内，而是上面的 Release 资产。
