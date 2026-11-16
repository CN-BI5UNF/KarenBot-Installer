# 安装引导 GUI 技术选型（Tkinter）

结论：安装器 GUI 使用 **Python 标准库 Tkinter + ttk**，不嵌入浏览器，
不采用 Tauri/WebView2/Electron，也不依赖被安装应用的 GTK4 运行时。
Linux 继续以 CLI 安装脚本为主，Windows 使用 PyInstaller 打包的 GUI。

## 为什么不用 WebView / 重型框架

| 方案 | 结论 |
|---|---|
| Tauri 2 + WebView2 | 不采用。需要 Rust 工具链和 WebView2 运行时，安装器为了一个向导引入两层新依赖，收益不大 |
| Electron | 不采用。100MB+、内存高、启动慢 |
| PySide6 / Qt | 不采用。安装器再带一套 Qt，体积和打包复杂度都高 |
| GTK4 / libadwaita | 不采用。安装器不能依赖自己正在安装的运行时；Windows 上 GTK4 便携打包也复杂 |
| WinUI 3 / WPF | 不采用。Windows 体验好，但自包含 .NET 体积大，且不能复用到 Linux |
| 纯浏览器本地页 | 不采用。窗口、端口、卸载和进度都不好控制 |
| **Tkinter + ttk** | **采用**。随 CPython 提供，无额外运行时；单文件 exe 小、启动快；跨 Windows/Linux；界面朴素但符合“简易安装引导”的定位 |

代价是 Tkinter 的视觉不如 Web 技术精致。这个安装器只需要选择来源、路径、
主题、进度和日志，不需要动画或复杂布局，因此这个取舍是合适的。

## 结构

本仓库只放安装器，不包含 KarenBot 宿主或模块源码：

```text
.
  karen_install.py             # 安装核心：测速、下载、校验、解压、venv、主题、字体、快捷方式
  gui_install.py               # Tkinter 向导，复用同一核心
  KarenBot-Installer.spec      # PyInstaller 单文件构建
  mirrors.json                 # GitHub 直连 + 公共前缀镜像
  windows-gui-package.json     # Windows GTK4 便携包契约
  install.sh                   # Linux NapCat 风格一行式入口
  requirements-build.txt       # 固定的打包工具链
  requirements-test.txt        # 固定的测试工具链
  packaging/                   # Windows 文件版本信息
  scripts/                     # Windows/Linux 构建与产物校验
  tests/                       # 核心、GUI flow、静态契约、Windows 打包契约
  docs/                        # Windows 预构建包与构建说明
  README.md
  gui-architecture.md          # 本文
  .github/workflows/           # Windows/Linux x64/arm64 构建
```

## GUI 布局

主窗口使用 `ttk.Notebook` 分成两页，避免小屏/高 DPI 时按钮被日志框挤出：

1. **安装配置**
   - 安装来源：在线 / 本地源码 / 本地安装包；
   - 界面版本：原生 GUI / WebUI；
   - 安装位置；
   - 高级设置（默认收起）：快捷方式、字体、PyPI/GitHub 镜像；
   - 外观与渲染：Karen/Adwaita/不使用主题包 + 自动/软件/硬件渲染。
2. **安装日志**
   - 完整安装日志、下载进度和错误信息。

底部固定操作栏：镜像测速、打开目录、启动、兼容模式启动、开始安装、取消。
“镜像测速”仍是独立窗口，展示镜像、速度、首字节延迟、评分和错误。

安装开始后会弹出模态进度窗口（`grab_set` 锁定主窗口），显示当前阶段
（自动测速、下载 backend、下载界面模块、创建 venv、下载字体、应用主题、
写入启动入口等），并提供取消按钮；“查看日志”会先释放模态并切换到日志页。
结束后自动关闭，失败时用错误对话框给出网络/路径/权限提示。
配置页放在可滚动 Canvas 里，小屏/高 DPI 不会裁掉渲染和主题选项。

## 安装核心

- 镜像测速：并发探测前 512KB，吞吐 + 首字节延迟评分，失败换镜像；
- Release 解析：`releases/latest` → `releases.atom`，页面/feed 先直连、超时后
  并发竞速镜像；直连 404 视为无稳定版，直接回退 atom；
- Linux 在线安装：backend 核心包 + GUI/WebUI 源码模块；
- Windows 原生 GUI：下载 `KarenBot-Windows-x64.zip`，按契约校验 GTK4 DLL/typelib；
- 本地安装：源码、tar/zip、预构建包；
- 字体：下载 Noto Sans SC / Noto Serif SC 可变字体，生成 `fonts.conf`；
- 主题：写 GUI 的 `karen-gui/skin` 偏好（karen/system），不使用第三方主题资产；
- 渲染：启动入口设置 `KAREN_GUI_RENDERER` / `GSK_RENDERER` 等环境变量，
  不向 GTK 传 `--renderer`；
- 快捷方式：Linux `.desktop`；Windows PowerShell `.lnk`，GUI 窗口最小化
  避免 `.cmd` 控制台遮挡。

## Windows 兼容性

- **Per-user 安装**：默认 `%LOCALAPPDATA%\Programs\KarenBot`，快捷方式写入
  当前用户的开始菜单/桌面，不触发 UAC。
- **高 DPI**：启动 Tk 前调用 `SetProcessDpiAwareness(1)`，避免系统位图拉伸；
  优先使用 Microsoft YaHei UI。
- **主题包**：GUI 的 `gui/theme.py` 读取 `%APPDATA%\karen-gui\skin` 或
  `%LOCALAPPDATA%\karen-gui\skin`；安装器同时写这两个位置，兼容 GLib 在
  Windows 上的目录映射。
- **字体**：写到 `%LOCALAPPDATA%\KarenBot\fonts`，启动 `.cmd` 设置
  `FONTCONFIG_FILE`，避免 GUI 启动器在 Windows 上错误地使用
  `~/.local/share`。
- **原生 GUI**：Windows 不能从源码安装；必须使用预构建 GTK4 便携包。
  安装器校验 `runtime/python.exe`、GTK4/Adwaita DLL、Gtk/Adw typelib。
- **运行数据**：启动器注入 `KAREN_DATA_ROOT=<安装目录>/data`，升级只替换
  `app/`；旧版 `app/data` 会迁移，避免覆盖安装清空用户数据。
- **Windows 卸载项**：把冻结安装器 exe（或源码脚本 + 系统 Python）复制到
  `%LOCALAPPDATA%\KarenBot\Uninstall`，并在 HKCU `...\Uninstall\KarenBot`
  写入卸载项；卸载进程在安装目录之外，能删除安装目录内的 Python 运行时，
  不依赖原始安装器 exe，也不需要管理员权限。
- **中文路径**：安装目录/源码/安装包路径含非 ASCII 字符时提前报错，并提示
  `%PUBLIC%\KarenBot`；默认目录若因用户名带中文，会自动改用 `%PUBLIC%`。
- **权限预检**：安装前写一个探针文件验证目录可写；不可写时提示改用
  per-user 目录，而不是等到 venv 阶段才报 `PermissionError`。
- **Python 自举**：系统 Python 缺失或不含 venv/ensurepip 时，下载
  python-build-standalone 便携 Python 3.12.16 到 `<install>/python-runtime`；
  全局 Python 完全不动。Linux 原生 GUI 仍要求系统 PyGObject/GTK4。

## Linux 兼容性

- CLI 仍是主要入口；`install.sh` 支持一行式引导，优先使用仓库内源码或
  同目录冻结版二进制，否则从 raw/镜像下载 `karen_install.py`。
- GUI 需要 `python3-tk` 和可用的中文字体。
- GUI 安装继续使用系统 PyGObject/GTK4；venv 带 `--system-site-packages`，
  并在安装后检查 `Gtk 4.0` / `Adw 1`。
- 启动器设置 `FONTCONFIG_FILE`；兼容模式 `launch-gui-safe` 额外设置
  `GSK_RENDERER=cairo` / `GDK_DEBUG=gl-disable` / `LIBGL_ALWAYS_SOFTWARE=1`。

## 构建与分发

`.github/workflows/build-installer.yml` 在**公开仓库的免费标准 runner** 上构建：

- `windows-2022`：PyInstaller windowed 单文件 exe（x64），校验 PE 架构、
  文件版本和 SHA256；
- `ubuntu-24.04` + `ubuntu:22.04` 容器：`KarenBot-Installer-linux-x64`；
- `ubuntu-24.04-arm` + `ubuntu:22.04` 容器：`KarenBot-Installer-linux-arm64`；
- `workflow_dispatch` 手动触发；PR 只跑 verify；推送 `v*` tag 时附加到 Release。

不构建 macOS（公开仓库也按 10 倍分钟计费，当前不需要）。安装器不内置
KarenBot/Python/GTK 包；只负责安装引导。不构建 deb/rpm。

## 签名与 SmartScreen

当前不购买 Authenticode 证书。未签名 exe 在 Windows 上可能触发 SmartScreen
“更多信息 → 仍要运行”。后续可以申请面向开源项目的免费签名服务，或购买
证书；这不影响功能开发。

## 当前阶段

- 已支持：在线 Release 安装、本地源码/安装包、镜像测速、主题包、字体、
  渲染回退启动器、快捷方式、程序与数据分离、Windows/Linux 单文件构建。
- Windows 原生 GUI 的 GTK4 便携包由安装器仓库的 Windows 打包 job 产出并
  附加到安装器 Release；安装器下载、校验和安装。
