# 构建与 CI

安装器的正式构建只在 GitHub Actions 上执行。目标机/本机内存有限，不要在
本机直接跑 PyInstaller；本地只做语法、静态检查和单元测试。

## GitHub Actions

`.github/workflows/build-installer.yml`：

| Job | Runner | 产物 |
|---|---|---|
| `verify` | `ubuntu-24.04` | 静态检查 + 核心/契约测试 |
| `build-windows` | `windows-2022` | `KarenBot-Installer.exe` + SHA256（≤60 MiB） |
| `build-windows-bundle` | `windows-2022` + gvsbuild GTK4 / Python 3.14 | `KarenBot-Windows-x64.zip`（GTK4 便携包，Release 资产，不放进 exe） |
| `build-linux-x64` | `ubuntu-24.04` + `ubuntu:22.04` 容器 | `KarenBot-Installer-linux-x64` |
| `build-linux-arm64` | `ubuntu-24.04-arm` + `ubuntu:22.04` 容器 | `KarenBot-Installer-linux-arm64` |
| `release` | `ubuntu-latest` | 推送 `v*` tag 时附加到 Release；同时从主仓库打包 `KarenBot-Personas.zip` |

Windows 工具链固定为 Python 3.12 + `requirements-build.txt` 中的
PyInstaller 版本，打包后用 `scripts/check_artifact_size.py` 强制检查
≤60 MiB。安装器不内置 Python；运行时按需下载到安装目录。`scripts/verify_windows_artifact.ps1` 会检查：

- PE machine `0x8664`（x64）；
- `FileVersionInfo` 是否写入；
- 嵌入清单是否启用 PerMonitorV2 DPI 感知和 longPathAware；
- 文件大小是否异常；
- `--check` / `--self-test` 退出码；
- SHA256。

`build-windows-bundle` 下载 gvsbuild GTK4 2026.8.0 和 python-build-standalone
CPython 3.14.10，并从 `CN-BI5UNF/KarenBot` 主仓库取 `personas/`，安装
gvsbuild 的 PyGObject/PyCairo wheels 与后端依赖，
产物 `KarenBot-Windows-x64.zip` 作为安装器 Release 资产，不进入安装器 exe；
详细目录契约见 `docs/windows-gui-package.md`。

Linux 在 Ubuntu 22.04 容器内构建，最低 glibc 2.35；不要在 Ubuntu 24.04
主机上直接构建后发布，否则产物会要求 glibc 2.38+，无法在 Debian 12 上启动。

## 手动触发

```bash
gh workflow run build-installer.yml --ref <branch>
gh run list --workflow build-installer.yml
gh run watch <run-id>
```

## 本地轻量验证（不打包）

```bash
python -m compileall -q karen_install.py gui_install.py
bash -n install.sh
python -m pytest tests/test_installer.py tests/test_windows_packaging.py -q
python -m ruff check karen_install.py gui_install.py tests/
```

GUI flow 测试需要显示环境：

```bash
xvfb-run -a python -m pytest tests/test_installer_gui_flow.py -q
xvfb-run -a python gui_install.py --self-test
xvfb-run -a python gui_install.py --smoke-test
```

`--self-test` 不联网，覆盖 tar.gz/zip 解压、SHA256、首包魔数、atom 解析、
缺失文件合并、数据根探测、启动器/安装标记和卸载脚本复制；冻结版 CI 会
实际运行它，用来兜住 PyInstaller excludes 误删依赖（例如 quopri）。

## 版本

- `VERSION` / `INSTALLER_VERSION`：`1.0.1-dev`
- Windows 文件版本：`packaging/windows-version-info.txt`（FileVersion `1.0.1`）
- 发布 tag：`v1.0.1-dev`（Pre-release）
