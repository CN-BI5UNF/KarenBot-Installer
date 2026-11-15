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
