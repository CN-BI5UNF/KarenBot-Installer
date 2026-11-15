# 用户反馈与处理

本文件记录实际测试中发现的安装器问题及当前实现。

## 1. 用户路径包含中文，导致无法安装

**现象**：Windows 用户名/下载目录带中文时，venv、pip 或 cmd 在路径处理上
失败。

**处理**：

- `path_problem()` 在 Windows 上检查安装目录、源码目录、安装包路径是否为
  非 ASCII；发现中文时直接报错，并给出 `C:\Users\Public\KarenBot` 示例。
- Windows 默认安装目录如果是 `%LOCALAPPDATA%\...` 且含中文，自动改用
  `%PUBLIC%\KarenBot`（例如 `C:\Users\Public\KarenBot`）。
- GUI 在开始安装前弹窗提示，不会进入后台安装后才失败。

## 2. 用户不点“测速镜像”，且连接 GitHub 失败

**现象**：用户不会主动点测速按钮；按指引点击后仍可能连不上 GitHub。

**处理**：

- 安装时自动对实际资产做镜像测速，用户不需要先点“镜像测速”。
- 新增模态进度窗口，显示“自动测速 / 下载 backend / 下载界面模块 /
  创建 venv / 下载字体 / 应用主题 / 写入启动入口”等阶段。
- 所有镜像都失败时明确提示检查网络、代理、DNS、防火墙，并提示
  `HTTPS_PROXY/HTTP_PROXY`；下载错误也会保留每个镜像的失败原因。
- 手动“镜像测速”窗口仍保留，供排查使用。

## 3. 非管理员运行 exe，创建 venv 权限不足

**现象**：用户把安装目录选到 `C:\Program Files` 等位置，venv 创建时报
`PermissionError`。

**处理**：

- 默认 per-user 安装到 `%LOCALAPPDATA%\Programs\KarenBot`，不触发 UAC。
- 安装前用临时探针文件验证目录可写；不可写时立即提示改用
  `%LOCALAPPDATA%\Programs\KarenBot` 或 `%PUBLIC%\KarenBot`。
- Windows 原生 GUI 改为安装 GTK4 便携预构建包，不再需要在本机创建 venv，
  从根源上避开了大部分权限问题。

## 4. 用户全局 Python 环境不完整

**现象**：系统 Python 缺少 venv/ensurepip/ssl/sqlite3，或根本没有 Python。

**处理**：

- 安装前检测 Python 3.10+ 及 `venv`、`ensurepip`、`ssl`、`sqlite3`；
  不完整则自动下载 python-build-standalone 便携 Python 3.12.16 到
  `<安装目录>/python-runtime`，不改动全局 Python。
- 安装器本身不内置 Python；成品限制 ≤60 MiB。
- 便携 Python 有固定 SHA256，并支持 GitHub 镜像测速/回退。
- 安装器成品由 CI 强制检查 ≤60 MiB。
- Linux 原生 GUI 仍要求系统 PyGObject/GTK4，安装器会给出 apt 安装命令；
  这是系统 GTK 绑定 ABI 的限制，便携 Python 无法替代。

## 5. 安装界面按钮/标题被裁切

**现象**：Windows 高 DPI 下，进度弹窗的“取消安装”按钮和顶栏
“KarenBot 安装引导”显示不全。

**处理**：

- 顶栏去掉固定 56px 高度，改由标签按当前字体度量撑开；
- 进度弹窗不再写死 230px 高度，控件排完后按 `winfo_reqheight()` 定高；
- 下载进度同时显示已下载/总大小、平均速度和预计剩余时间。

## 6. 首次配置无法跳过、字段乱填导致 RootConfig 校验失败

**现象**：用户不会填 API Key 时被卡在向导；把 `agents.summary.provider`
手填成 `12` 后，后端报“provider 未在 providers 中定义”。

**处理**：

- GUI 向导新增“稍后配置”（顶栏与底部），确认后不写配置直接进入控制台；
  Runtime 不启动，顶栏“继续配置”可随时回到向导。
- 子 Agent、视觉、Embedding 的 provider 改为只列出已定义 provider 的
  下拉框；`_payload()` 再做一次回落，旧值不会写进 config.yaml。
- 模型 ID、Base URL、API Key、天气主机、SearXNG 地址等字段限制为
  英文/合法格式；中文或空格会在向导内直接提示。
- 新增“恢复默认”按钮，确认后清空本次填写并回到第一步。

## 7. 滚轮经过数值框会误改设置

**现象**：滚动设置页时鼠标经过“请求超时”等 SpinButton，数值被悄悄改掉。

**处理**：GUI 所有 SpinButton 改用统一的 `widgets.spin_button()`；未获得
焦点时吞掉滚轮事件，必须先点进输入框才能用滚轮微调。

## 8. Runtime 报“未找到任何可用人格”

**现象**：Windows 便携包/在线安装后 `app/personas` 只有空的
`__init__.py`，Runtime 启动失败。

**处理**：

- Windows 便携包在 CI 中从 `CN-BI5UNF/KarenBot` 主仓库复制 `personas/`，
  并加入包契约检查；
- Linux/WebUI 在线安装在安装阶段下载 `KarenBot-Personas.zip`，缺失时
  回退主仓库源码包；
- 旧版便携包会被安装器拒绝，并提示使用包含人格的新版包。

## 9. Windows 日志中的 dbus 警告

`GLib-GIO-WARNING: win32 session dbus binary not found` 是 Windows 上没有
D-Bus 会话总线时 GLib 打印的警告，不影响 GUI 启动；同段日志里真正导致
Runtime 退出的是缺少 `personas/*/persona_prompt.py`，已按第 8 节处理。

## 10. 升级失败后旧版本消失

**现象**：安装器先执行 `app/ → app.old`，再放入新 `app/`；如果后续
建 venv、装依赖或写启动入口失败，旧版本会被留在 `app.old`，用户没有
可用的 `app/`。

**处理**：替换 `app/` 后记录回滚点；任何失败/取消都会删除半成品并把
`app.old` 换回 `app/`。只有全部步骤成功后才删除旧备份。

## 11. Windows 启动后残留 cmd 黑框

**现象**：双击桌面快捷方式后，任务栏一直挂着一个命令提示符窗口。

**处理**：

- `launch-gui.cmd` 优先使用同目录的 `pythonw.exe`，并用 `start "" /B`
  让 cmd 立即退出；
- `launch-gui-safe.cmd` 继续使用 `python.exe`，崩溃时 `pause`，方便查看
  报错；
- 快捷方式优先使用预构建 exe 的图标，否则使用便携包里的
  `gui/assets/karenbot.ico` 或安装器内置 `karenbot.ico`。

## 12. 安装到一半点“取消”没有反应

**现象**：点击取消后，`pip install` 或解压仍然继续，进度窗口卡住。

**处理**：

- 下载按 256KB 分块、解压按成员循环检查取消事件；
- `pip`/`venv` 子进程改为 Popen 轮询，取消时在 Windows 用
  `taskkill /F /T` 终止整个进程树，在 Linux 用进程组 SIGTERM；
- 子进程输出实时转发到日志页，失败时能看到 pip 的真实报错。

## 13. 升级时旧版本仍在后台运行

**现象**：替换 `app/` 时报 `WinError 5`；或上次残留的 `app.old` 被占用，
`rename` 报 `WinError 183`。

**处理**：

- 安装前和替换前各检查一次 `app/` 下的运行中进程和 exe/dll 占用；
- 删除 `app.old` 失败时给出具体路径并中止，不再静默忽略；
- 替换失败会提示先关闭 KarenBot，避免下载几百 MB 后才崩溃。

## 14. 取消/断电后 venv 半残，重装一直失败

**现象**：创建 venv 阶段取消或断电后，`<安装目录>/venv` 目录还在，但
`python.exe`/`pip` 不完整；再次安装时 `_create_venv` 只判断目录存在就复用，
随后 `pip install` 必定失败。

**处理**：托管 venv 在复用前执行完整性探测（能运行并导入 `ssl`/`sqlite3`）；
不完整则自动删除重建。用户通过 `--venv` 显式指定的环境不会被自动删除。

## 15. 中文用户名下 fontconfig 启动慢/缺字

**现象**：`%LOCALAPPDATA%\KarenBot\fonts` 路径含中文，libfontconfig 读取
`FONTCONFIG_FILE` 或写缓存异常，GTK 每次启动重新扫描字体。

**处理**：Windows 下检测字体目录是否为纯 ASCII；不是则改用
`<安装目录>\fonts`（安装目录本身已通过 ASCII 校验），启动器随之设置新的
`FONTCONFIG_FILE`。

## 16. 镜像返回 HTTP 200 错误页却被测速当成“快”

**现象**：公共反代限流时返回 `{"error": ...}` 或 `Too Many Requests`，
体积小、延迟低，测速评分反而很高，真正下载才失败。

**处理**：探测时校验 HTTP 状态码、`Content-Type`、文件魔数（gzip/zip/exe/
字体等）和错误文本；`.tar.gz` 必须是 gzip 魔数，`.zip/.whl` 必须是 `PK`。

## 17. 下载到 90% 断线后从头开始

**处理**：失败/取消时保留 `.part`；下一次尝试发送 `Range: bytes=<已有>-`，
服务端返回 206 时追加写入，返回 200 时覆盖重下；最终仍以 SHA256 校验。

## 18. 无哈希源码包静默走第三方镜像

**处理**：backend/frontend 源码归档没有发布哈希时默认仅 GitHub 直连；
GUI 首次在线安装会弹窗让用户选择“允许镜像加速 / 仅直连 / 取消”，CLI 可用
`--allow-unverified` 或 `KAREN_ALLOW_UNVERIFIED=1` 放行，也可用
`KAREN_BACKEND_SHA256` / `KAREN_GUI_SHA256` / `KAREN_WEBUI_SHA256` 提供哈希。

## 19. 安装完成前关闭窗口导致旧版本不回滚

**现象**：升级到一半点右上角关闭，daemon 线程被直接结束，`app.old` 没有
换回 `app`。

**处理**：关闭时先请求取消，然后每 100ms 等待工作线程完成回滚和 staging
清理；最多等 30 秒才强制退出。

## 20. Windows 杀软误报/启动慢

**处理**：进程占用检测改用 Toolhelp32 快照（ctypes），快捷方式清理直接
删除 `.lnk`，不再为这两件事启动 PowerShell；创建快捷方式合并为一次
PowerShell 调用，并移除了 `-ExecutionPolicy Bypass`。
