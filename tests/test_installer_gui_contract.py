"""安装器 GUI 的技术约束与构建入口契约。

安装器刻意使用 Tkinter：不引入 WebView/Tauri/Electron，也不依赖被安装的
GTK4 运行时。这里用静态契约锁住这个决定，并确保主题/渲染/字体入口和
GitHub Actions 构建入口不会在后续改动中丢失。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT


def test_gui_uses_tkinter_not_webview():
    source = (INSTALLER / "gui_install.py").read_text(encoding="utf-8")
    lowered = source.casefold()
    assert "import tkinter as tk" in source
    assert "from tkinter import" in source
    assert "webview" not in lowered
    assert "webview2" not in lowered
    assert "tauri" not in lowered
    assert "electron" not in lowered
    # 安装器不得依赖 GTK4；它安装的应用才依赖 GTK4。
    assert "import gi" not in source


def test_gui_wires_core_sinks_cancel_and_options():
    source = (INSTALLER / "gui_install.py").read_text(encoding="utf-8")
    assert "set_log_sink" in source
    assert "set_progress_sink" in source
    assert "InstallCancelled" in source
    assert "cancel_event" in source
    assert "--smoke-test" in source
    assert "--check" in source
    assert "ONLINE_TAG" in source
    assert 'value="online"' in source
    assert "gui_skin" in source
    assert "render_mode" in source
    assert "launch_safe_button" in source
    assert "_open_progress_dialog" in source
    assert "path_problem" in source
    assert "ensure_writable_dir" in source
    assert "set_status_sink" in source


def test_gui_has_windows_compatibility_hooks():
    source = (INSTALLER / "gui_install.py").read_text(encoding="utf-8")
    assert "SetProcessDpiAwareness" in source
    assert "Microsoft YaHei UI" in source
    assert "os.startfile" in source or "startfile" in source
    assert "self_delete_installer" in source
    assert "不使用美化包" in source
    assert "兼容模式启动" in source


def test_pyinstaller_spec_bundles_resources_and_version():
    spec = (INSTALLER / "KarenBot-Installer.spec").read_text(encoding="utf-8")
    assert "mirrors.json" in spec
    assert "windows-gui-package.json" in spec
    assert "windows-version-info.txt" in spec
    assert 'hiddenimports=["karen_install"]' in spec
    assert 'console=not sys.platform.startswith("win")' in spec


def test_github_actions_builds_installer_on_free_runners():
    workflow = (ROOT / ".github/workflows/build-installer.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch" in workflow
    assert "windows-2022" in workflow
    assert "ubuntu-24.04" in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "scripts/build_windows.ps1" in workflow
    assert "scripts/build_linux.sh" in workflow
    assert '"v*"' in workflow
    assert "installer-windows-x64" in workflow
    assert "installer-linux-x64" in workflow
    assert "installer-linux-arm64" in workflow
    # macOS runner 对公开仓库也按 10 倍分钟计费；当前不需要。
    assert "macos" not in workflow.casefold()


def test_architecture_document_records_tkinter_and_windows_decisions():
    document = (INSTALLER / "gui-architecture.md").read_text(encoding="utf-8")
    assert "Tkinter" in document
    assert "WebView2" in document
    assert "不采用" in document or "不推荐" in document or "放弃" in document
    assert "windows-gui-package.json" in document
    assert "launch-gui-safe" in document


def test_gui_exposes_mirror_and_uninstall_controls():
    source = (INSTALLER / "gui_install.py").read_text(encoding="utf-8")
    assert "github_mirror" in source
    assert "_apply_selected_mirror" in source
    assert "uninstall_button" in source
    assert "core.uninstall" in source
    assert "_config_widgets" in source
