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

- Windows 安装器内置 python-build-standalone stripped Python 3.12.16
  （约 22 MB，含 pip/venv），安装时解压到
  `<安装目录>/python-runtime`，不改动全局 Python。
- 内置运行时缺失/损坏时，安装前检测 Python 3.10+ 及 `venv`、
  `ensurepip`、`ssl`、`sqlite3`；不完整则自动下载完整便携 Python。
- 便携 Python 有固定 SHA256，并支持 GitHub 镜像测速/回退。
- 安装器成品（含内置 Python）由 CI 强制检查 ≤ 50 MiB。
- Linux 原生 GUI 仍要求系统 PyGObject/GTK4，安装器会给出 apt 安装命令；
  这是系统 GTK 绑定 ABI 的限制，便携 Python 无法替代。
