"""Windows 打包契约、CI 工具链和 install.sh 引导入口的静态测试。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT


def test_windows_gui_package_contract_is_valid():
    contract = json.loads(
        (INSTALLER / "windows-gui-package.json").read_text(encoding="utf-8")
    )
    assert contract["schema"] == 1
    assert contract["asset_name"] == "KarenBot-Windows-x64.zip"
    assert "runtime/python.exe" in contract["python_candidates"]
    assert "gtk-4-1.dll" in contract["required_dlls"]
    assert "Gtk-4.0.typelib" in contract["required_typelibs"]
    assert "Adw-1.typelib" in contract["required_typelibs"]
    assert contract["runtime_env"]["XDG_DATA_DIRS"] == "runtime/share"


def test_spec_bundles_contract_and_windows_version_info():
    spec = (INSTALLER / "KarenBot-Installer.spec").read_text(encoding="utf-8")
    assert "mirrors.json" in spec
    assert "windows-gui-package.json" in spec
    assert "windows-version-info.txt" in spec
    assert 'hiddenimports=["karen_install"]' in spec
    assert 'console=not sys.platform.startswith("win")' in spec


def test_windows_build_scripts_are_pinned_and_verify_artifact():
    build = (INSTALLER / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )
    verify = (INSTALLER / "scripts" / "verify_windows_artifact.ps1").read_text(
        encoding="utf-8"
    )
    assert "requirements-build.txt" in build
    assert "PyInstaller KarenBot-Installer.spec" in build
    assert "0x8664" in verify
    assert "Get-FileHash" in verify
    assert "SHA256SUMS-windows.txt" in verify
    assert "--check" in verify


def test_requirements_pin_toolchain():
    build = (INSTALLER / "requirements-build.txt").read_text(encoding="utf-8")
    test = (INSTALLER / "requirements-test.txt").read_text(encoding="utf-8")
    assert "pyinstaller==" in build
    assert "pytest==" in test
    assert "ruff==" in test


def test_workflow_builds_windows_and_linux_without_deb():
    workflow = (ROOT / ".github/workflows/build-installer.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch" in workflow
    assert "windows-2022" in workflow
    assert "ubuntu-24.04" in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "ubuntu:22.04" in workflow
    assert "scripts/build_windows.ps1" in workflow
    assert "scripts/verify_windows_artifact.ps1" in workflow
    assert "requirements-build.txt" in workflow
    assert "SHA256SUMS" in workflow
    assert "installer-windows-x64" in workflow
    assert "installer-linux-x64" in workflow
    assert "installer-linux-arm64" in workflow
    assert "dpkg-deb" not in workflow
    assert "macos" not in workflow.casefold()


def test_install_sh_is_napcat_style_bootstrap():
    script = (INSTALLER / "install.sh").read_text(encoding="utf-8")
    assert "karen_install.py" in script
    assert "KAREN_INSTALLER_REF" in script
    assert "KAREN_INSTALLER_SHA256" in script
    assert "raw.githubusercontent.com" in script
    assert "cdn.jsdelivr.net" in script
    assert "ghproxy.net" in script
    assert "KAREN_INSTALLER_BASE" in script
    # 冻结版是 GUI 入口；带 CLI 参数时必须继续用 Python 核心。
    assert '[ "$#" -eq 0 ]' in script


def test_readme_documents_no_local_build_and_no_deb():
    readme = (INSTALLER / "README.md").read_text(encoding="utf-8")
    assert "install.sh" in readme
    assert "windows-gui-package.json" in readme
    assert "KarenBot-Windows-x64.zip" in readme
    assert "不构建 deb" in readme or "不构建 deb/rpm" in readme
    assert "GitHub Actions" in readme


def test_bundled_python_fetch_and_size_scripts():
    fetch = (INSTALLER / "scripts" / "fetch_bundled_python.py").read_text(
        encoding="utf-8"
    )
    size = (INSTALLER / "scripts" / "check_artifact_size.py").read_text(
        encoding="utf-8"
    )
    assert "BUNDLED_PYTHON_SOURCES" in fetch
    assert "sha256" in fetch
    assert "vendor" in fetch
    assert "50" in size
    assert "max-mib" in size


def test_workflow_builds_windows_bundle_asset():
    workflow = (ROOT / ".github/workflows/build-installer.yml").read_text(
        encoding="utf-8"
    )
    assert "build-windows-bundle" in workflow
    assert "KarenBot-Windows-x64.zip" in workflow
    assert "gvsbuild" in workflow
    assert "fetch_bundled_python.py" in workflow
    assert "check_artifact_size.py" in workflow
    assert "--max-mib 50" in workflow


def test_windows_bundle_script_uses_gvsbuild_and_standalone_python():
    script = (INSTALLER / "scripts" / "build_windows_bundle.py").read_text(
        encoding="utf-8"
    )
    assert "gvsbuild" in script
    assert "pygobject" in script
    assert "python.exe" in script
    assert "girepository-1.0" in script
    assert "gdk-pixbuf-query-loaders.exe" in script
    assert "windows-bundle.json" in script
    assert "-m" in script and "gui" in script
    assert "KarenBot-Windows-x64" in script


def test_spec_can_bundle_vendor_python():
    spec = (INSTALLER / "KarenBot-Installer.spec").read_text(encoding="utf-8")
    assert "vendor" in spec
    assert "python-runtime.tar.gz" in spec
    assert "python-runtime.sha256" in spec
