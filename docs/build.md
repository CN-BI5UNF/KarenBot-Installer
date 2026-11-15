# 构建与 CI

安装器的正式构建只在 GitHub Actions 上执行。目标机/本机内存有限，不要在
本机直接跑 PyInstaller；本地只做语法、静态检查和单元测试。

## GitHub Actions

`.github/workflows/build-installer.yml`：

| Job | Runner | 产物 |
|---|---|---|
| `verify` | `ubuntu-24.04` | 静态检查 + 核心/契约测试 |
| `build-windows` | `windows-2022` | `KarenBot-Installer.exe` + SHA256（内置 stripped Python，≤50 MiB） |
| `build-windows-bundle` | `windows-2022` + MSYS2 UCRT64 | `KarenBot-Windows-x64.zip`（GTK4 便携包，Release 资产，不放进 exe） |
| `build-linux-x64` | `ubuntu-24.04` + `ubuntu:22.04` 容器 | `KarenBot-Installer-linux-x64` |
| `build-linux-arm64` | `ubuntu-24.04-arm` + `ubuntu:22.04` 容器 | `KarenBot-Installer-linux-arm64` |
| `release` | `ubuntu-latest` | 推送 `v*` tag 时附加到 Release |

Windows 工具链固定为 Python 3.12 + `requirements-build.txt` 中的
PyInstaller 版本；Windows job 会先用 `scripts/fetch_bundled_python.py`
把 stripped Python 3.12.16 放进 `vendor/`，打包后用
`scripts/check_artifact_size.py` 强制检查 ≤50 MiB。`scripts/verify_windows_artifact.ps1` 会检查：

- PE machine `0x8664`（x64）；
- `FileVersionInfo` 是否写入；
- 文件大小是否异常；
- `--check` 退出码；
- SHA256。

`build-windows-bundle` 在 MSYS2 UCRT64 里组装 GTK4/PyGObject/后端依赖，
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
xvfb-run -a python gui_install.py --smoke-test
```

## 版本

- `VERSION` / `INSTALLER_VERSION`：`0.1.0-dev`
- Windows 文件版本：`packaging/windows-version-info.txt`（FileVersion `0.1.0`）
- 发布 tag：`v0.1.0-dev`（Pre-release）
