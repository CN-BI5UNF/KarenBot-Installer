# KarenBot 安装引导器

这是 KarenBot 的**独立安装引导仓库**，与模块仓库解耦。提供 Linux CLI
（NapCat 风格的一行式入口）、跨平台 Tkinter GUI、GitHub 镜像测速、在线
Release 安装、本地源码/离线包安装、GTK4 主题包选择、字体自动拉取、渲染
回退启动器和快捷方式。

安装器**不内置任何应用包**，也不构建 deb/exe 应用包；它只负责选择来源、
测速、下载、校验、解压、建 venv 和写启动入口。Linux 以终端 CLI 为主，
Windows 原生 GUI 通过安装器 Release 里的 GTK4 便携预构建包安装。

仓库：`https://github.com/CN-BI5UNF/KarenBot-Installer`（当前版本
`v1.0.0-rc`，安装器版本 `1.0.0-rc`）

## 快速开始

### Linux 一行式安装（CLI）

`raw.githubusercontent.com` 在部分网络会被重置（`curl: (35) Recv failure:
Connection reset by peer`）。下面这条命令会依次尝试多个镜像，成功拿到
`install.sh` 后再执行：

```bash
sha=v1.0.0-rc
want=2358b7ea0e1e8bcf421db5c4d93d370b421170839a8ff78c9d456c8e97558b57
core=77c8c296c77839bda148b2921961ee9cd2f261414449dd2ed19da39fd18dc2b8
KAREN_INSTALLER_REF=$sha KAREN_INSTALLER_SHA256=$core bash <(
  ok=0
  for u in \
    "https://gh-proxy.com/https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/$sha/install.sh" \
    "https://ghproxy.net/https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/$sha/install.sh" \
    "https://ghproxy.cfd/https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/$sha/install.sh" \
    "https://fastly.jsdelivr.net/gh/CN-BI5UNF/KarenBot-Installer@$sha/install.sh" \
    "https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/$sha/install.sh"; do
      s="$(curl -fsSL --connect-timeout 8 --max-time 30 "$u" 2>/dev/null)" || continue
      case "$s" in
        "#!/usr/bin/env bash"*) ;;
        *) continue ;;
      esac
      if command -v sha256sum >/dev/null 2>&1; then
        h="$(printf '%s' "$s" | sha256sum | cut -d' ' -f1)"
      else
        h="$(printf '%s' "$s" | shasum -a 256 | cut -d' ' -f1)"
      fi
      [ "$h" = "$want" ] || { echo "跳过 SHA256 不匹配的源：$u" >&2; continue; }
      printf '%s\n' "$s"
      ok=1
      break
  done
  [ "$ok" = 1 ] || printf 'echo "无法下载 install.sh：请检查网络或使用代理" >&2\nexit 1\n'
)
```

上面的命令把 bootstrap 和安装核心都固定在 tag `v1.0.0-rc`，并分别用
SHA256 校验脚本和 `karen_install.py`；这样即使使用第三方镜像，也不会执行
被替换的旧脚本。想跟随 `main` 最新代码时，去掉 `KAREN_INSTALLER_REF` 和
`KAREN_INSTALLER_SHA256` 即可。

如果网络能直连 GitHub，也可以用原来的短命令：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/main/install.sh)
```

所有源都失败时，请配置代理，或下载仓库里的 `install.sh` 后本地执行
`bash install.sh`。`install.sh` 自身也会按 `raw → jsDelivr → ghproxy`
的顺序回退获取 `karen_install.py`，可用 `KAREN_INSTALLER_BASE` 固定镜像。

没有参数时会进入交互菜单；也可以直接传参：

```bash
# 在线安装原生 GUI（Linux 使用系统 GTK4）
./install.sh --frontend gui --online

# 在线安装 WebUI
./install.sh --frontend webui --online

# 本地源码安装（开发/本机验证）
./install.sh --frontend gui \
  --source-dir /path/to/KarenBot-GUI \
  --dir ~/.local/opt/karenbot --no-shortcut

# 本地 tar/zip 安装包
./install.sh --frontend gui --archive /path/to/karen.tar.gz

# 只测镜像速度
./install.sh --probe-url \
  https://github.com/CN-BI5UNF/KarenBot-backend/archive/refs/tags/v0.1.1-alpha.1.tar.gz
```

### GUI

```bash
# Linux 需要 Tkinter（Debian/Ubuntu）
sudo apt install python3-tk
python3 gui_install.py
```

Windows 使用 GitHub Actions 产出的 `KarenBot-Installer.exe`，不需要本机
Python；也可以在本机用 PyInstaller 构建（见下文，仅验证用）。Linux 的
`KarenBot-Installer-linux-*` 是 GUI 入口；带 CLI 参数时 `install.sh` 会改用
Python 安装核心，避免把 `--frontend` 等参数传给 GUI。

### GUI 与 WebUI 怎么选

- **原生 GUI**：桌面原生窗口，图片预览、默认应用打开、文件夹定位更顺手；
  Linux 需要系统 GTK4/libadwaita/PyGObject，Windows 使用便携 GTK4 预构建包。
- **WebUI**：浏览器访问，不需要 GTK，局域网内手机/平板也能用；文件以浏览器
  下载、内嵌打开为主，需要保持服务运行并管理访问令牌。

## 安装器做了什么

1. **镜像测速自动选择**：`mirrors.json` 内置 GitHub 直连和多个公共镜像；
   并发探测资产前 256KB，按吞吐 + 首字节延迟评分，自动选最快且低延迟的
   方案；下载失败自动换下一个镜像。同一次安装内复用测速结果（5 分钟
   窗口），不会为每个资产重复测速。Release 页面/feed 解析也采用并发竞速：
   先请求直连，1.5 秒无响应就并发启动镜像，首个有效响应立即胜出；直连
   404 视为权威结论直接回退 `releases.atom`，不再串行等待所有镜像。
2. **在线 Release 安装**：Linux 下载 backend 核心包 + GUI/WebUI 源码模块；
   Windows 原生 GUI 下载 `KarenBot-Windows-x64.zip` 便携包。
3. **内置人格补齐**：backend 仓库的 `personas/` 只有空壳；Windows 便携包
   在 CI 中从主仓库复制人格，Linux/WebUI 在线安装在安装阶段下载
   `KarenBot-Personas.zip`（失败时回退主仓库源码包），避免 Runtime 因
   “没有可用人格”拒绝启动。
4. **本地安装**：源码目录、tar/zip、预构建包；不需要 GitHub。
5. **GTK4 主题包**：Windows GUI 可选“Karen 暖色（目标机同款）”“系统
   Adwaita”或“不使用美化包”。前两者都是 GTK4 内置皮肤，不需要下载主题
   资产；选择不使用时会明确提醒 Windows 观感问题需要自行解决。
6. **字体自动拉取**：安装时下载 GUI 实际使用的 Noto Sans SC / Noto Serif SC
   可变字体，写到 GUI 读取的位置，并生成只追加该目录的 `fonts.conf`；
   不把字体打进安装器。字体候选源（raw.githubusercontent.com、jsdelivr
   等）会先并发探测前 256KB，按评分排序后再下载，避免在失效源上逐个
   等待超时；最终仍用内置 SHA256 校验。
7. **渲染回退**：启动入口在 GTK 初始化前设置渲染环境；GUI 自带的
   `gui.launcher` 还会按 stage 自动回退到 Cairo 软件渲染。另提供
   `launch-gui-safe` / “KarenBot GUI (兼容模式)”入口。
8. **快捷方式**：Linux `.desktop`，Windows 一次 PowerShell 调用创建全部
   开始菜单/桌面快捷方式；进程检测和快捷方式清理改用 ctypes / 直接删除，
   不再额外拉起 PowerShell。便携包没有 exe 时使用内置 `karenbot.ico`。
   安装器完成安装后始终保留，方便后续修复或卸载。
9. **可取消与实时日志**：下载、解压、`venv`/`pip` 子进程都会响应“取消”；
   `pip` 输出实时转发到“安装日志”页，失败时能看到真实报错。
10. **安装前预检**：检查目录可写、磁盘余量（GUI 约 1.2 GB / WebUI 约
    400 MB）以及旧版 KarenBot 是否仍在运行，避免升级到最后一步才因文件
    占用失败。
11. **卸载**：CLI `--uninstall` 或 GUI “卸载…” 按钮会清理程序目录、
    启动器、快捷方式、字体、主题偏好和运行数据目录，但只对带安装标记的
    目录生效。`--keep-data` / GUI 卸载确认框的“是”只卸载程序，保留
    `data/` 运行数据，并把 `app/personas` 备份到 `data/personas`；下次
    安装会自动把自定义人格合并回 `app/personas`。
12. **残损环境自愈**：被取消/杀软中断后留下的半残 `venv` 会在下次安装时
    自动检测并重建，不需要手工删除。
13. **断点续传**：带 SHA256 的资产中断后保留 `.part` 和来源标记；只有同一
    镜像 URL 才发送 `Range` 续传，切换镜像或缺少哈希时重新下载，避免跨源
    字节拼接。旧版没有来源标记的临时文件只在带 SHA256 时允许续传，最终
    校验兜底。
14. **日志与诊断**：安装日志实时落盘到 `%TEMP%\karenbot-install.log`，
    GUI 提供“复制日志 / 保存日志 / 查看日志”；进度窗以模态方式锁定主窗口，
    避免安装/卸载过程中重复点击配置控件，“查看日志”会先释放模态再切页。
15. **已有安装可直接管理**：启动安装器时如果目标目录已安装，会直接启用
    “打开安装目录 / 启动 / 兼容模式启动”，不需要先重装一次。
16. **程序与数据分离**：新版 backend 支持 `KAREN_DATA_ROOT`；启动器把运行
    数据固定在 `<安装目录>/data/instances/default`，升级只替换 `app/`，
    不会碰用户配置、聊天库、工作区或自定义人格。旧版 `app/data` 会在
    升级时自动迁移；backend 不支持该变量时回退到 `app/data` 迁移。
17. **Windows 卸载入口**：安装时把冻结安装器 exe（源码运行时为安装器核心
    脚本 + 系统 Python）复制到 `%LOCALAPPDATA%\KarenBot\Uninstall`，并在
    `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\KarenBot`
    写入免管理员权限的卸载项。卸载进程在安装目录之外运行，因此能删除
    安装目录内的 `app/`、`venv/` 和运行数据；即使原始安装器 exe 已删除，
    用户仍可在 Windows“设置 → 应用”中直接卸载。注册表分别写入交互式
    `UninstallString` 和带 `--non-interactive` 的 `QuietUninstallString`，
    并写入 `EstimatedSize`；从“设置 → 应用”点击卸载会先弹出“保留数据 /
    彻底删除 / 取消”三态确认。卸载流程会清理注册表项；卸载器副本因正在
    运行可能保留，可稍后手动删除。冻结安装器 exe 也支持
    `--uninstall --dir <安装目录> --non-interactive [--keep-data]`，
    便于脚本化卸载。
18. **小屏友好**：镜像/字体/外观等选项默认收进“高级设置”，默认窗口高度
    降到 560~640；1366×768 或 1080p 150% 缩放下打开即可看到“开始安装”。

## 已知问题处理

更详细的“现象 → 处理”记录见 [`docs/known-issues.md`](docs/known-issues.md)。
针对实际用户反馈，安装器内置了以下保护：

1. **Windows 中文路径**：安装目录、源码目录、安装包路径会检查非 ASCII
   字符；发现中文时直接提示修改，并给出 `C:\Users\Public\KarenBot` 示例。
   Windows 默认目录如果因为用户名是中文而带中文，会自动改用
   `%PUBLIC%\KarenBot`，避免 venv/pip/cmd 在中文路径下失败。
2. **自动测速与步骤提示**：安装开始时自动测速并选择镜像，不需要用户先点
   “镜像测速”。GUI 会弹出“正在安装”进度窗口，实时显示“自动测速 / 下载
   backend / 下载界面模块 / 创建 venv / 下载字体 / 应用主题 / 写入启动入口”
   等阶段；所有镜像都失败时会明确提示检查网络、代理、DNS 或防火墙。
3. **权限预检**：安装前先验证安装目录可写。默认 per-user 目录不需要管理员；
   如果用户选了 `C:\Program Files` 等不可写目录，会立即提示改用
   `%LOCALAPPDATA%\Programs\KarenBot` 或 `%PUBLIC%\KarenBot`，不会等到
   venv 阶段才报错。
4. **Python 环境自举**：安装前检测 Python 3.10+ 是否含 `venv`/`ensurepip`/
   `ssl`/`sqlite3`；不完整时自动下载 python-build-standalone 的便携 Python
   3.12.16 到 `<安装目录>/python-runtime`，只用于本安装，不改动用户全局
   Python。安装器本身不内置 Python，成品限制在 **60 MiB** 以内。Linux
   原生 GUI 例外：它仍需要系统 PyGObject/GTK4，安装器会给出 apt 安装命令。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--frontend gui\|webui` | 选择原生 GUI 或 WebUI |
| `--online` | 从 GitHub Release 在线安装；未指定源码/安装包时默认开启 |
| `--tag` | 指定 Online Release tag；默认 `auto`（每个仓库优先稳定版，否则最新版） |
| `--dir` | 安装目录，Linux 默认 `~/.local/opt/karenbot`，Windows 默认 `%LOCALAPPDATA%\Programs\KarenBot` |
| `--source-dir` | 从本地源码安装（测试/离线；Windows 原生 GUI 不支持源码安装） |
| `--archive` | 从本地 tar/zip 安装包安装（可安装 Windows GTK4 便携包） |
| `--venv` | 复用已有 venv（测试用） |
| `--skip-deps` | 跳过 venv/依赖安装（测试用） |
| `--no-fonts` | 不下载 Noto 中文字体 |
| `--no-shortcut` | 不创建快捷方式 |
| `--gui-skin karen\|system\|none` | GUI 主题包；默认 `karen`（目标机同款） |
| `--renderer auto\|cairo\|gl` | 渲染模式；`cairo` 强制软件渲染，黑屏/崩溃时使用 |
| `--mirror <名称>` | 固定使用某个镜像；`--list-mirrors` 查看名称 |
| `--allow-unverified` | 允许无发布哈希的源码包走第三方镜像（有篡改风险） |
| `--require-verified` | 严格模式：任何未校验的可执行/源码资产都中止 |
| `--windows-gui-asset URL` | 覆盖 Windows GUI 预构建包 URL（本地路径或测试镜像） |
| `--bundle-tag <tag>` | Windows GUI 便携包所在的安装器 Release tag；默认 `auto` |
| `--uninstall` | 卸载 `--dir` 指向的 KarenBot，并清理快捷方式/字体/主题偏好/运行数据 |
| `--keep-data` | 卸载时保留 `data/` 运行数据，并备份 `app/personas` 到 `data/personas` |
| `--probe-url` | 只对镜像做测速并退出 |
| `--list-mirrors` | 列出内置镜像后退出 |
| `--dry-run` | 打印最终配置，不执行安装 |

## 镜像与安全

`mirrors.json` 维护 GitHub 直连和公共前缀镜像。测速并发请求实际资产的前
256KB，按吞吐和首字节延迟评分；下载失败自动换下一个镜像。Release 元数据
（`releases/latest` / `releases.atom`）先直连，超过 1.5 秒再并发启动镜像；
直连 404 视为没有稳定版/Release，直接回退 atom。

第三方镜像能看到下载请求，也可能篡改内容。安装器对可执行资产执行以下策略：

- `SHA256SUMS.txt` 优先从 GitHub 直连读取；来自第三方镜像的清单标记为不可信，
  不能用于可执行资产校验；
- Windows GUI 便携包必须拿到 GitHub 直连的 SHA256 清单，否则报错中止；
  确认风险时可设置 `KAREN_ALLOW_UNVERIFIED=1`；
- 人格资产缺少可信哈希时，回退下载主仓库源码包；回退包在无哈希时强制直连；
- backend/frontend 的 GitHub 自动源码归档没有发布方哈希，安装器默认**仅使用
  GitHub 直连**；需要第三方镜像加速时必须显式设置 `KAREN_ALLOW_UNVERIFIED=1`
  或提供下面的哈希（GUI 会弹窗确认）：

| 环境变量 | 作用 |
|---|---|
| `KAREN_BACKEND_SHA256` | 校验 backend 源码归档 |
| `KAREN_GUI_SHA256` / `KAREN_WEBUI_SHA256` | 校验所选界面模块源码归档 |
| `KAREN_PERSONAS_SHA256` | 校验人格回退用的主仓库源码归档 |
| `KAREN_WINDOWS_GUI_SHA256` | 校验自定义/覆盖的 Windows GUI 便携包 |
| `KAREN_REQUIRE_VERIFIED=1` | 严格模式：缺少上述哈希时直接中止，不允许未校验下载 |
| `KAREN_ALLOW_UNVERIFIED=1` | 明确允许未校验下载（仅用于测试/可信网络） |

`install.sh` 还支持 `KAREN_INSTALLER_SHA256` 校验 `karen_install.py`；README
顶部的一行式命令已经同时固定了 commit 和核心哈希。

Release 流水线会为 Windows 便携包、人格包、安装器本体，以及
`karen_install.py`、`gui_install.py`、`install.sh` 等安装器源码生成
`SHA256SUMS.txt`；`install.sh` 也会作为 Release 资产上传。

## Windows 原生 GUI

Windows 源码安装不现实：GTK4/PyGObject 需要一整套便携运行时。安装器改为
下载安装器 Release 的预构建包 `KarenBot-Windows-x64.zip`，并按
`windows-gui-package.json` 契约校验：

- `runtime/python.exe`（启动器会优先使用同目录的 `pythonw.exe`，避免黑框）；
- `gtk-4-1.dll`、`adwaita-1-0.dll`、`gobject-2.0-0.dll`；
- `Gtk-4.0.typelib`、`Adw-1.typelib`；
- `backend/` + `gui/`。

详细目录与构建工具链约定见 [`docs/windows-gui-package.md`](docs/windows-gui-package.md)。
如果当前 Release 还没有该资产，安装器会给出明确错误；可用
`KAREN_GUI_WINDOWS_ASSET=<本地或远程 zip>` / `--windows-gui-asset` 临时覆盖。

## 字体

默认下载 Noto Sans SC / Noto Serif SC 可变字体（与 GUI `gui/style.css`、
`gui/fonts.py` 使用的字体一致）到：

- Linux：`$XDG_DATA_HOME/KarenBot/fonts`（默认 `~/.local/share/KarenBot/fonts`）
- Windows：`%LOCALAPPDATA%\KarenBot\fonts`；如果用户名导致该路径含中文，
  自动改用 `<安装目录>\fonts`（安装目录已通过纯 ASCII 校验）

并生成只追加该目录的 `fonts.conf`。启动器会设置 `FONTCONFIG_FILE`，GUI
不会再回退到 Windows 默认宋体/黑体。字体下载失败不会阻塞安装。

## 渲染回退

GUI 的 `gui/launcher.py` 在导入 `gi` 之前按 stage 设置环境变量：

- stage 1：`GSK_RENDERER=cairo`、`GDK_DEBUG=gl-disable`、
  `LIBGL_ALWAYS_SOFTWARE=1`；
- stage 2：再加 `GDK_BACKEND=x11`（Linux 有 DISPLAY 时）/ `win32`（Windows）。

安装器生成的 `launch-gui` 默认使用自动回退；`launch-gui-safe` 直接从
Cairo 软件渲染开始，桌面快捷方式也提供“KarenBot GUI (兼容模式)”。安装器
不会把 `--renderer` 传给 GTK（GUI 启动器只从环境变量读取），避免未知参数。

## 构建（GitHub Actions，免费）

`.github/workflows/build-installer.yml` 使用公开仓库的免费标准 runner：

- `windows-2022` → `KarenBot-Installer.exe`（x64，windowed，≤60 MiB）；
- `windows-2022` + gvsbuild GTK4 / CPython 3.14 → `KarenBot-Windows-x64.zip`（GTK4 便携包，Release 资产，不放进 exe）；
- `ubuntu-24.04` runner + `ubuntu:22.04` 容器 → `KarenBot-Installer-linux-x64`（x64，保留 console）；
- `ubuntu-24.04-arm` runner + `ubuntu:22.04` 容器 → `KarenBot-Installer-linux-arm64`（arm64，保留 console）；
- `workflow_dispatch` 手动触发；PR 只跑 verify；
- 推送 `v*` tag 时自动把安装器、Windows 便携包、人格资产
  `KarenBot-Personas.zip` 和 `SHA256SUMS.txt` 附加到 GitHub Release。

Windows 构建使用 `requirements-build.txt` 固定 PyInstaller 版本，由
`scripts/build_windows.ps1` 打包、`scripts/verify_windows_artifact.ps1` 校验
PE x64、文件版本、大小和 SHA256，并由 `scripts/check_artifact_size.py`
强制检查 ≤60 MiB。Windows GUI 便携包由
`scripts/build_windows_bundle.py` 用 gvsbuild GTK4 2026.8.0 +
CPython 3.14.10 组装，CI 还会用安装器核心实际安装并运行一次启动器。Linux 在 Ubuntu 22.04 容器内构建，最低
glibc 2.35（Ubuntu 22.04 / Debian 12 及以上）。

**不要在本机直接 PyInstaller 打包**（内存不够时容易失败）；本地只做语法/
单测验证，完整构建和测试交给 Actions。本地验证：

```bash
python -m pytest tests/test_installer.py tests/test_windows_packaging.py -q
python -m ruff check karen_install.py gui_install.py tests/
bash -n install.sh
```

不构建 macOS（公开仓库也按 10 倍分钟计费，当前不需要）。构建产物未签名，
Windows 可能提示 SmartScreen，可选择“更多信息 → 仍要运行”。

## 安全边界

- 解压拒绝 `..`、绝对路径、符号链接和设备文件；Python 3.12+ 使用 PEP 706
  的 `filter="data"`，旧版本回退到手工校验；
- 下载支持 SHA256 校验，失败自动换镜像；校验清单优先直连读取，第三方镜像
  提供的清单不用于可执行资产；
- Windows GUI 便携包缺少可信 SHA256 时中止，除非显式设置
  `KAREN_ALLOW_UNVERIFIED=1`；
- Windows 默认 per-user 安装，不触发 UAC；卸载项写入 HKCU
  `...\Uninstall\KarenBot`，卸载完成后删除；
- 安装器只替换 `<install>/app`；新版 backend 的运行数据固定在
  `<install>/data/instances/default`，升级不会删除；旧版 `app/data` 会在
  升级时迁移。升级失败会自动把 `app.old` 换回 `app/`；覆盖安装前会检测
  被占用的 exe/dll；
- 安装器完成安装后始终保留，不再提供自删除（降低杀软启发式误报，也方便
  用户再次运行修复/卸载）；
- Windows GUI 预构建包按 DLL/typelib 契约检查，缺运行时直接报错；
- 卸载只对带 `.karen-installer.json`（或 `app/modules.json` installer 字段）
  的目录生效，避免误删用户指定目录；
- 卸载默认删除运行数据；`--keep-data` 或 GUI 确认框选“是”时只删除程序，
  保留 `data/` 并把 `app/personas` 备份到 `data/personas`，下次安装恢复。

## 当前限制

- 在线安装默认自动选择每个仓库的最新**稳定版**；没有稳定版时选择最新
  Release。backend 提供核心程序 + 后端，GUI/WebUI 各自提供界面模块。
- Windows 原生 GUI 需要安装器 Release 里已有 `KarenBot-Windows-x64.zip`；
  它由安装器仓库的 Windows 打包 job 产出，不放进安装器 exe。
- Windows WebUI 安装会按需下载便携 Python；系统 Python 不是必需。
- 不构建 deb/rpm 等系统包：Linux 用户以 CLI/install.sh 为主。
- GUI 是简易向导，不是完整安装器设计系统；Linux 仍以 CLI 为主。
