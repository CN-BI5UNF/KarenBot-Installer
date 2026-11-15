# KarenBot 安装引导器

这是 KarenBot 的**独立安装引导仓库**，与模块仓库解耦。提供 Linux CLI
和跨平台 Tkinter GUI，支持在线 Release 安装、本地源码/离线安装包、镜像测速、
字体下载、快捷方式和自删除。安装器不内置应用包，也不编译 deb/exe。

仓库：`https://github.com/CN-BI5UNF/KarenBot-Installer`（当前版本 `v0.0.1-dev`）

## 快速开始

### Linux CLI（主要入口）

```bash
# 在线安装（默认；下载 backend 核心包 + 所选界面包）
python3 karen_install.py --frontend webui --online

# 本地源码安装（开发和本机验证）
python3 karen_install.py \
  --frontend gui \
  --source-dir /path/to/KarenBot-GUI \
  --dir ~/.local/opt/karenbot \
  --no-shortcut

# 本地 tar/zip 安装包
python3 karen_install.py --frontend gui --archive /path/to/karen.tar.gz

# 只测镜像速度
python3 karen_install.py --probe-url \
  https://github.com/CN-BI5UNF/KarenBot/archive/refs/tags/v1.0.1-alpha.1.tar.gz
```

### GUI

```bash
# Linux 需要 Tkinter（Debian/Ubuntu）
sudo apt install python3-tk
python3 gui_install.py
```

Windows 使用 GitHub Actions 产出的 `KarenBot-Installer.exe`，不需要本机
Python；也可以在本机用 PyInstaller 构建（见下文）。Linux GUI 是可选入口，
终端用户仍推荐 CLI 或直接使用 `install.sh`。

### GUI 与 WebUI 怎么选

- **原生 GUI**：桌面原生窗口，图片预览、默认应用打开、文件夹定位更顺手；
  需要系统 GTK4/libadwaita/PyGObject，Windows 原生 GUI 暂不支持。
- **WebUI**：浏览器访问，不需要 GTK，局域网内手机/平板也能用；文件以浏览器
  下载、内嵌打开为主，需要保持服务运行并管理访问令牌。

GUI 功能：

- 在线安装（自动选择最新稳定版，无稳定版时使用最新 Release；下载 backend 核心包 + GUI/WebUI）；
- 本地源码目录 / 本地 tar/zip；
- 原生 GUI 或 WebUI；
- 安装目录、快捷方式、中文字体、安装后自删除；
- 独立镜像测速窗口（并发探测前 256KB，按吞吐和首字节延迟排序）；
- 后台安装、进度、日志、取消、打开目录/启动。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--frontend gui\|webui` | 选择原生 GUI 或 WebUI |
| `--online` | 从 GitHub Release 在线安装；未指定源码/安装包时默认开启 |
| `--tag` | 指定 Online Release tag；默认 `auto`（每个仓库优先稳定版，否则最新版） |
| `--dir` | 安装目录，Linux 默认 `~/.local/opt/karenbot`，Windows 默认 `%LOCALAPPDATA%\Programs\KarenBot` |
| `--source-dir` | 从本地源码安装（测试/离线） |
| `--archive` | 从本地 tar/zip 安装包安装 |
| `--venv` | 复用已有 venv（测试用） |
| `--skip-deps` | 跳过 venv/依赖安装（测试用） |
| `--no-fonts` | 不下载 Noto 中文字体 |
| `--no-shortcut` | 不创建快捷方式 |
| `--self-delete` | 安装后删除安装器自身 |
| `--keep-installer` | 打包版安装后保留安装器（覆盖默认自删除） |
| `--probe-url` | 只对镜像做测速并退出 |

## 镜像

`mirrors.json` 维护 GitHub 直连和公共前缀镜像。测速并发请求实际资产的前
256KB，按吞吐和首字节延迟排序；下载失败自动换下一个镜像。字体下载也走同一
套逻辑，失败不会阻塞安装。

第三方镜像能看到下载请求，也可能篡改内容。安装器对有 SHA256 的资产会校验；
没有官方哈希的 Release tarball 只能靠结构和运行检查。敏感环境请优先使用
`direct`，或在后续 Release manifest 中补齐哈希。

## 字体

默认下载 Noto Sans SC / Noto Serif SC 可变字体到：

- Linux：`$XDG_DATA_HOME/KarenBot/fonts`
- Windows：`%LOCALAPPDATA%\KarenBot\fonts`

并生成只追加该目录的 `fonts.conf`。GUI 没有字体时会回退到系统字体。

## 构建（GitHub Actions，免费）

`.github/workflows/build-installer.yml` 使用公开仓库的免费标准 runner：

- `windows-latest` → `KarenBot-Installer.exe`（x64，windowed，无黑框）；
- `ubuntu-24.04` runner + `ubuntu:22.04` 容器 → `KarenBot-Installer-linux-x64`（x64，保留 console）；
- `ubuntu-24.04-arm` runner + `ubuntu:22.04` 容器 → `KarenBot-Installer-linux-arm64`（arm64，保留 console）；
- `workflow_dispatch` 手动触发；
- 推送 `v*` tag 时自动把三个产物附加到 GitHub Release（当前版本 `v0.0.1-dev`，会标记为 Pre-release）。

Linux 产物在 Ubuntu 22.04 容器内构建，最低要求 **glibc 2.35**
（Ubuntu 22.04 / Debian 12 及以上）。不要在 Ubuntu 24.04 主机上直接构建后
发布，否则产物会要求 glibc 2.38+，无法在 Debian 12 或更旧的系统上启动。

不构建 macOS（公开仓库也按 10 倍分钟计费，当前不需要）。构建产物未签名，
Windows 可能提示 SmartScreen，可选择“更多信息 → 仍要运行”；后续可申请
开源签名服务或购买证书。

本地构建仅用于验证：

```bash
python -m pip install pyinstaller
python -m PyInstaller KarenBot-Installer.spec --noconfirm --clean
python gui_install.py --check
python gui_install.py --smoke-test   # Linux 需要 DISPLAY/xvfb
```

## 安全边界

- 解压拒绝 `..`、绝对路径、符号链接和设备文件；
- 下载支持 SHA256 校验，失败自动换镜像；
- Windows 默认 per-user 安装，不触发 UAC；
- 安装器只替换 `<install>/app`，不删除用户数据目录；
- 自删除只在打包版默认开启；源码运行默认保留脚本。

## 当前限制

- 在线安装默认自动选择每个仓库的最新**稳定版**；没有稳定版时选择最新
  Release。backend 提供核心程序 + 后端，GUI/WebUI 各自提供界面模块。
  如果两个仓库的最新版本不兼容，可显式指定 `--tag` 使用同一版本。
- **Windows 源码/在线 GUI 不支持**：GTK4/PyGObject 需要 Windows 便携运行时；
  在线 WebUI 安装需要系统 Python 3.10+，或使用预构建安装包。
- **Windows GTK4 应用包尚未构建**：安装器已能识别预构建 `.exe` 包，但应用
  本身的 Windows 构建链是独立任务。
- GUI 是简易向导，不是完整安装器设计系统；Linux 仍以 CLI 为主。
