# Windows GTK4 GUI 便携包契约

KarenBot 安装器不在 Windows 上从源码安装原生 GUI。GTK4 + libadwaita +
PyGObject 在 Windows 上需要一整套便携运行时，因此由 **本仓库的 GitHub
Actions（`build-windows-bundle` job）** 用 gvsbuild GTK4 +
python-build-standalone 组装，附加到安装器自己的 Release；安装器只负责
下载、校验和安装。GUI 仓库不需要发布任何资产。

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
  personas/                        # 主仓库 KarenBot 的内置人格
    大肥鱼/persona_prompt.py
    deepseek/persona_prompt.py
  main.py                          # KarenBot 宿主入口
  windows-bundle.json              # KarenBot 发行版 tag + backend/GUI/personas/Python/GTK 版本
  runtime/
    python.exe
    pythonw.exe
    python314.dll
    Lib/                           # 标准库 + site-packages（pip、gi、后端依赖）
    bin/
      gtk-4-1.dll
      adwaita-1-0.dll
      gobject-2.0-0.dll
      gdk-pixbuf-query-loaders.exe
      ...                          # gvsbuild 的 GTK/Pango/GDK DLL
    lib/
      girepository-1.0/
        Gtk-4.0.typelib
        Adw-1.typelib
        ...
      gdk-pixbuf-2.0/2.10.0/loaders/
      gio/modules/
    share/
      glib-2.0/schemas/            # 含编译后的 gschemas.compiled
      icons/
      gtk-4.0/
    etc/fonts/
```

安装器按 `windows-gui-package.json` 检查：

- `required_dirs`: `backend`, `gui`, `personas`；
- `personas/` 下必须至少有一个 `*/persona_prompt.py`（Runtime 没有可用
  人格会拒绝启动）；
- `python_candidates`: `runtime/python.exe` 等；
- `required_dlls`: `gtk-4-1.dll`, `adwaita-1-0.dll`,
  `gobject-2.0-0.dll`；
- `typelib_dirs` + `required_typelibs`: `Gtk-4.0.typelib`, `Adw-1.typelib`。

缺任何一项都会直接失败，并提示按本契约重新打包，避免安装后黑屏/缺字。

## 构建工具链

`.github/workflows/build-installer.yml` 的 `build-windows-bundle` job：

1. checkout 本仓库，以及 `CN-BI5UNF/KarenBot` 主仓库的 `v2.0.0-alpha`
   （含 `modules/KarenBot-backend`、`modules/KarenBot-GUI` submodule 和
   `personas/`）；`--release-tag` 可手动指定；
2. 下载并校验 **gvsbuild GTK4 2026.8.0 x64**（MSVC ABI，含 GTK4、
   libadwaita、PyGObject/PyCairo cp314 wheels 和全部依赖）：

   ```text
   https://github.com/wingtk/gvsbuild/releases/download/2026.8.0/GTK4_Gvsbuild_2026.8.0_x64.zip
   SHA256 1f95a92d037f5292da05e6ab1037032ff21ddb7b20d4ac8e83e3674c864c07b0
   ```

3. 下载并校验 **python-build-standalone CPython 3.14.10**（stripped install_only）：

   ```text
   cpython-3.14.10+20261110-x86_64-pc-windows-msvc-install_only_stripped.tar.gz
   SHA256 32b6cf90a03e2bc15fda8d2ab7ff78c4870f138887d612932650ce764efaab8f
   ```

4. 运行 `scripts/build_windows_bundle.py`：
   - 解压 Python 和 gvsbuild；
   - 复制 `backend/` + `gui/`；
   - 从主仓库复制 `personas/`，并冒烟检查至少一个人格包含
     `persona_prompt.py`；
   - 安装 gvsbuild 的 `PyGObject` / `PyCairo` wheels；
   - `pip install` backend 依赖（使用 CPython MSVC ABI，PyPI wheel 均可用，
     不再受 MSYS2 mingw ABI 限制）；
   - 复制 GTK DLL、typelib、gdk-pixbuf loaders、Gio 模块、icons、schemas、
     fontconfig；
   - 复制 VC++ runtime DLL；
   - 生成 gdk-pixbuf `loaders.cache`；
   - 写 `windows-bundle.json`；
   - 冒烟 `import gi`、backend 依赖、`python -m gui --version`；
   - 生成 `KarenBot-Windows-x64.zip`。
5. 上传 artifact；推送 `v*` tag 时由 `release` job 附加到安装器 Release。

安装器安装后会按最终安装路径重新生成
`runtime/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache`，避免打包机绝对路径失效。

## 运行时环境

安装器生成的 `launch-gui.cmd` / `launch-gui-safe.cmd` 会设置：

```text
PATH=<app>\runtime\bin;<app>\runtime;%PATH%
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

`launch-gui.cmd` 优先调用同目录的 `pythonw.exe`（`start ""` 后立即
`exit /b 0`，无残留黑框）；`launch-gui-safe.cmd` 使用 `python.exe` 并在
失败时 `pause`，方便查看日志。启动器同时注入 `KAREN_DATA_ROOT`，把运行
数据固定在 `<安装目录>\data\instances\default`。

## 快捷方式图标

安装器优先使用预构建 exe 的图标；没有 exe 时依次查找
`app/gui/assets/karenbot.ico`、`app/assets/karenbot.ico`、
`app/karenbot.ico`，最后回退到安装器内置的 `karenbot.ico`。便携包构建脚本
会把该图标复制到 `gui/assets/karenbot.ico`。

## 主题、字体与渲染

- 主题包由 GUI 内置：`karen`（目标机同款）和 `system`（Adwaita）。安装器
  写入 `%APPDATA%\karen-gui\skin` 和 `%LOCALAPPDATA%\karen-gui\skin`；
- 不内置第三方 GTK4 主题资产，避免许可证和资源路径问题；
- 字体由安装器下载 Noto Sans SC / Noto Serif SC 到
  `%LOCALAPPDATA%\KarenBot\fonts`，并生成 `fonts.conf`；
- `gui/launcher.py` 在导入 `gi` 前按 stage 回退；安装器的
  `launch-gui-safe.cmd` 从 Cairo 软件渲染开始。

## 安装器自身大小

安装器本身**不内置 Python**：检测到系统 Python 3.10+ 不完整时，才下载
python-build-standalone 3.12.16 到 `<安装目录>/python-runtime`。安装器 exe
成品由 CI 强制检查 ≤60 MiB。GTK4 大运行时不在 exe 内，而是上面的
Release 资产。
