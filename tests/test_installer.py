"""Linux 安装引导器的本地/离线测试。"""

from __future__ import annotations

import http.server
import io
import json
import os
import socketserver
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

import karen_install as installer


@pytest.fixture
def local_server(tmp_path):
    payload = b"karen-installer-test" * 4096
    file_path = tmp_path / "asset.bin"
    file_path.write_bytes(payload)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp_path), **kwargs)

        def log_message(self, *_args):
            return

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}/asset.bin", payload
        finally:
            server.shutdown()


def test_mirrored_url_direct_and_prefix():
    direct = {"name": "direct", "kind": "direct"}
    prefix = {"name": "p", "kind": "prefix", "prefix": "https://mirror.test/https://github.com"}
    url = "https://github.com/owner/repo/archive/v1.tar.gz"
    assert installer.mirrored_url(url, direct) == url
    assert installer.mirrored_url(url, prefix) == (
        "https://mirror.test/https://github.com/owner/repo/archive/v1.tar.gz"
    )


def test_download_from_local_server(local_server, tmp_path, monkeypatch):
    url, payload = local_server
    monkeypatch.setattr(
        installer, "_MIRROR_CACHE", [{"name": "direct", "kind": "direct", "enabled": True}]
    )
    destination = tmp_path / "downloaded.bin"
    installer.download(url, destination)
    assert destination.read_bytes() == payload


def test_download_verifies_sha256(local_server, tmp_path, monkeypatch):
    url, _payload = local_server
    monkeypatch.setattr(
        installer, "_MIRROR_CACHE", [{"name": "direct", "kind": "direct", "enabled": True}]
    )
    destination = tmp_path / "bad.bin"
    with pytest.raises(SystemExit):
        installer.download(url, destination, expected_sha256="0" * 64)


def test_safe_tar_extract_rejects_traversal(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(SystemExit):
        installer._safe_tar_extract(archive, tmp_path / "out")


def test_find_app_root(tmp_path):
    root = tmp_path / "bundle"
    (root / "backend").mkdir(parents=True)
    (root / "gui").mkdir()
    assert installer._find_app_root(root) == root


def test_install_local_source_builds_launchers(tmp_path):
    source = tmp_path / "source"
    (source / "backend").mkdir(parents=True)
    (source / "gui").mkdir()
    (source / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    install_dir = tmp_path / "install"
    installer.install(
        frontend="gui",
        install_dir=install_dir,
        source_dir=source,
        archive=None,
        venv=Path(__file__).resolve().parents[1] / ".venv",
        skip_deps=True,
        create_shortcut=False,
        download_fonts=False,
    )
    assert (install_dir / "app" / "backend").is_dir()
    launcher_name = "launch-gui.cmd" if installer.IS_WINDOWS else "launch-gui.sh"
    launcher = (install_dir / launcher_name).read_text(encoding="utf-8")
    assert "-m gui" in launcher
    if not installer.IS_WINDOWS:
        assert str(install_dir / "app") in launcher
        assert "cd " in launcher
        safe = (install_dir / "launch-gui-safe.sh").read_text(encoding="utf-8")
        assert "GSK_RENDERER=cairo" in safe
        assert "GDK_DEBUG=gl-disable" in safe
        assert "LIBGL_ALWAYS_SOFTWARE=1" in safe


def test_install_fonts_writes_fontconfig(tmp_path, monkeypatch):
    root = tmp_path / "fonts"
    monkeypatch.setattr(installer, "_font_dir", lambda: root)

    def fake_download(url, destination, expected_sha256="", **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"font")
        return destination

    monkeypatch.setattr(installer, "download", fake_download)
    installer.install_fonts()
    assert (root / "NotoSansSC-VF.otf").is_file()
    assert (root / "NotoSerifSC-VF.otf").is_file()
    assert (root / "fonts.conf").is_file()


def test_probe_mirrors_returns_failures(local_server):
    url, _payload = local_server
    mirrors = [
        {"name": "direct", "kind": "direct", "enabled": True},
        {
            "name": "broken",
            "kind": "prefix",
            "prefix": "http://127.0.0.1:1",
            "enabled": True,
        },
    ]
    results = installer.probe_mirrors(url, mirrors, timeout=2)
    assert len(results) == 2
    assert any(item.get("ok") for item in results)
    assert any(not item.get("ok") for item in results)


def test_venv_python_layout(monkeypatch):
    venv = Path("/tmp/karen-venv")
    monkeypatch.setattr(installer, "IS_WINDOWS", False)
    assert installer._venv_python(venv) == venv / "bin" / "python"
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    assert installer._venv_python(venv) == venv / "Scripts" / "python.exe"


def test_install_archive_with_prebuilt_executable(tmp_path):
    import zipfile

    archive = tmp_path / "KarenBot-Windows.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("KarenBot-GUI.exe", b"fake-exe")
        zf.writestr("runtime/data.txt", "ok")
    install_dir = tmp_path / "install"
    installer.install(
        frontend="gui",
        install_dir=install_dir,
        source_dir=None,
        archive=archive,
        venv=None,
        skip_deps=False,
        create_shortcut=False,
        download_fonts=False,
    )
    app = install_dir / "app"
    assert (app / "KarenBot-GUI.exe").is_file()
    if not installer.IS_WINDOWS:
        assert (app / "KarenBot-GUI.exe").stat().st_mode & 0o111
    assert not (install_dir / "venv").exists()
    launcher_name = "launch-gui.cmd" if installer.IS_WINDOWS else "launch-gui.sh"
    launcher = (install_dir / launcher_name).read_text(encoding="utf-8")
    assert "KarenBot-GUI.exe" in launcher
    manifest = (app / "modules.json").read_text(encoding="utf-8")
    assert '"installer": "prebuilt-archive"' in manifest


def test_windows_shortcut_script_quotes_paths():
    script = installer._windows_shortcut_script(
        Path(r"C:\Program Files\KarenBot\KarenBot-GUI.exe"),
        Path(r"C:\Users\O'Brien\KarenBot"),
        "KarenBot GUI.lnk",
        Path(r"C:\Program Files\KarenBot\KarenBot-GUI.exe"),
        window_style=7,
    )
    assert "GetFolderPath('Desktop')" in script
    assert "O''Brien" in script
    assert "New-Object -ComObject WScript.Shell" in script
    assert "$s.WindowStyle = 7" in script


def test_install_local_source_writes_manifest(tmp_path):
    source = tmp_path / "source"
    (source / "backend").mkdir(parents=True)
    (source / "gui").mkdir()
    install_dir = tmp_path / "install"
    installer.install(
        frontend="gui",
        install_dir=install_dir,
        source_dir=source,
        archive=None,
        venv=Path(__file__).resolve().parents[1] / ".venv",
        skip_deps=True,
        create_shortcut=False,
        download_fonts=False,
    )
    manifest = (install_dir / "app" / "modules.json").read_text(encoding="utf-8")
    assert '"installer": "local-source"' in manifest


def test_windows_launchers_use_scripts_python(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    app = tmp_path / "app"
    app.mkdir()
    venv = tmp_path / "venv"
    installer._write_launchers(tmp_path, app, venv, "gui")
    raw = (tmp_path / "launch-gui.cmd").read_bytes()
    text = raw.decode("utf-8")
    assert "python.exe" in text
    assert "-m gui" in text
    assert b"\r\n" in raw


def test_install_rejects_source_inside_install_dir(tmp_path):
    source = tmp_path / "source"
    (source / "backend").mkdir(parents=True)
    (source / "gui").mkdir()
    install_dir = source / "install-target"
    with pytest.raises(SystemExit):
        installer.install(
            frontend="gui",
            install_dir=install_dir,
            source_dir=source,
            archive=None,
            venv=None,
            skip_deps=True,
            create_shortcut=False,
            download_fonts=False,
        )


def _tar_gz(path: Path, files: dict[str, bytes]) -> bytes:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path.read_bytes()


def test_find_core_and_frontend_roots(tmp_path):
    core = tmp_path / "backend-extract" / "KarenBot-backend-abc"
    core.mkdir(parents=True)
    (core / "main.py").write_text("", encoding="utf-8")
    (core / "backend").mkdir()
    (core / "core").mkdir()
    assert installer._find_core_root(core.parent) == core
    front = tmp_path / "frontend-extract" / "KarenBot-WebUI-abc"
    (front / "webui").mkdir(parents=True)
    (front / "webui" / "plugin.json").write_text("{}", encoding="utf-8")
    assert installer._find_frontend_root(front.parent, "webui") == front


def test_install_online_composes_backend_and_frontend(tmp_path, monkeypatch):
    backend_archive = tmp_path / "backend.tar.gz"
    backend_bytes = _tar_gz(
        backend_archive,
        {
            "KarenBot-backend-test/main.py": b"",
            "KarenBot-backend-test/backend/__init__.py": b"",
            "KarenBot-backend-test/core/__init__.py": b"",
            "KarenBot-backend-test/personas/example/persona_prompt.py": b"",
            "KarenBot-backend-test/pyproject.toml": b"[project]\nname='demo'\n",
        },
    )
    frontend_archive = tmp_path / "frontend.tar.gz"
    frontend_bytes = _tar_gz(
        frontend_archive,
        {
            "KarenBot-WebUI-test/webui/plugin.json": b"{}",
            "KarenBot-WebUI-test/webui/__init__.py": b"",
        },
    )

    def fake_download(url, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if "KarenBot-backend" in url:
            destination.write_bytes(backend_bytes)
        else:
            destination.write_bytes(frontend_bytes)
        return destination

    monkeypatch.setattr(installer, "download", fake_download)
    install_dir = tmp_path / "install"
    installer.install(
        frontend="webui",
        install_dir=install_dir,
        source_dir=None,
        archive=None,
        venv=tmp_path / "venv",
        skip_deps=True,
        create_shortcut=False,
        download_fonts=False,
        online=True,
        release_tag="v0.1.1-alpha.1",
    )
    app = install_dir / "app"
    assert (app / "main.py").is_file()
    assert (app / "backend").is_dir()
    assert (app / "core").is_dir()
    assert (app / "webui" / "plugin.json").is_file()
    manifest = json.loads((app / "modules.json").read_text(encoding="utf-8"))
    assert manifest["installer"] == "online-release"
    assert manifest["components"]["backend"]["tag"] == "v0.1.1-alpha.1"
    assert manifest["components"]["webui"]["repo"] == installer.ONLINE_FRONTEND_REPOS["webui"]


def test_release_tag_from_atom():
    data = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <link rel="alternate" href="https://github.com/demo/repo/releases/tag/v1.2.3"/>
      </entry>
    </feed>"""
    assert installer._release_tag_from_atom(data) == "v1.2.3"


def test_resolve_online_tags_uses_release_policy(monkeypatch):
    def fake_latest(repo, **_kwargs):
        if repo == installer.ONLINE_BACKEND_REPO:
            return "v1.2.0", "稳定版"
        return "v1.1.0", "最新版"

    monkeypatch.setattr(installer, "_latest_release_tag", fake_latest)
    backend_tag, frontend_tag = installer._resolve_online_tags(
        "webui", "auto"
    )
    assert backend_tag == "v1.2.0"
    assert frontend_tag == "v1.1.0"


def test_online_explicit_tag_bypasses_release_queries(monkeypatch):
    def fail_lookup(*_args, **_kwargs):
        raise AssertionError("explicit tag must not query GitHub")

    monkeypatch.setattr(installer, "_latest_release_tag", fail_lookup)
    assert installer._resolve_online_tags("gui", "v9.9.9") == (
        "v9.9.9",
        "v9.9.9",
    )


def test_mirror_score_prefers_throughput_and_low_latency():
    assert installer.mirror_score(2048, 0.1) > installer.mirror_score(2048, 1.0)
    assert installer.mirror_score(4096, 0.2) > installer.mirror_score(2048, 0.2)


def test_rank_mirrors_orders_by_score(monkeypatch):
    mirrors = [
        {"name": "low-latency", "kind": "direct"},
        {"name": "fast", "kind": "direct"},
        {"name": "dead", "kind": "direct"},
    ]
    results = [
        {
            "ok": True,
            "mirror": "fast",
            "url": "https://example.test/a",
            "speed": 3000.0,
            "latency": 0.9,
            "score": installer.mirror_score(3000.0, 0.9),
        },
        {
            "ok": True,
            "mirror": "low-latency",
            "url": "https://example.test/b",
            "speed": 2048.0,
            "latency": 0.1,
            "score": installer.mirror_score(2048.0, 0.1),
        },
        {"ok": False, "mirror": "dead", "error": "timeout"},
    ]

    monkeypatch.setattr(
        installer,
        "probe_mirrors",
        lambda _url, _mirrors=None, **_kwargs: results,
    )
    ranked = installer.rank_mirrors("https://example.test/asset", mirrors)
    assert [item["mirror"] for item in ranked] == ["low-latency", "fast"]


def test_available_mirrors_can_be_forced(monkeypatch):
    mirrors = [
        {"name": "direct", "kind": "direct"},
        {"name": "mirror", "kind": "prefix", "prefix": "https://mirror.test"},
    ]
    monkeypatch.setattr(installer, "_MIRROR_CACHE", mirrors)
    installer.set_mirror_override("mirror")
    assert [item["name"] for item in installer.available_mirrors()] == ["mirror"]
    installer.set_mirror_override("auto")
    assert len(installer.available_mirrors()) == 2


def test_apply_gui_skin_writes_all_known_locations(tmp_path, monkeypatch):
    dirs = [tmp_path / "roaming" / "karen-gui", tmp_path / "local" / "karen-gui"]
    monkeypatch.setattr(installer, "_gui_skin_dirs", lambda: dirs)
    installer.apply_gui_skin("karen")
    assert all((dir / "skin").read_text(encoding="utf-8").strip() == "karen" for dir in dirs)
    installer.apply_gui_skin("system")
    assert all((dir / "skin").read_text(encoding="utf-8").strip() == "system" for dir in dirs)
    installer.apply_gui_skin("none")


def test_render_env_modes():
    assert installer._render_env("auto") == {}
    cairo = installer._render_env("cairo")
    assert cairo["KAREN_GUI_RENDERER"] == "cairo"
    assert cairo["GSK_RENDERER"] == "cairo"
    assert cairo["GDK_DEBUG"] == "gl-disable"
    assert cairo["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert installer._render_env("gl")["KAREN_GUI_RENDERER"] == "gl"


def test_launcher_sets_fontconfig_and_renderer(tmp_path, monkeypatch):
    font_root = tmp_path / "fonts"
    font_root.mkdir()
    (font_root / "fonts.conf").write_text("<fontconfig/>", encoding="utf-8")
    monkeypatch.setattr(installer, "_font_dir", lambda: font_root)

    source = tmp_path / "source"
    (source / "backend").mkdir(parents=True)
    (source / "gui").mkdir()
    install_dir = tmp_path / "install"
    installer.install(
        frontend="gui",
        install_dir=install_dir,
        source_dir=source,
        archive=None,
        venv=tmp_path / "venv",
        skip_deps=True,
        create_shortcut=False,
        download_fonts=False,
        gui_skin="none",
        render_mode="cairo",
    )
    launcher = (install_dir / "launch-gui.sh").read_text(encoding="utf-8")
    assert str(font_root / "fonts.conf") in launcher
    assert "GSK_RENDERER=cairo" in launcher
    safe = (install_dir / "launch-gui-safe.sh").read_text(encoding="utf-8")
    assert "GSK_RENDERER=cairo" in safe


def _make_portable_app(root: Path) -> Path:
    app = root / "KarenBot-Windows-x64"
    (app / "backend").mkdir(parents=True)
    (app / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (app / "gui").mkdir()
    (app / "gui" / "plugin.json").write_text("{}", encoding="utf-8")
    persona = app / "personas" / "example"
    persona.mkdir(parents=True)
    (persona / "persona_prompt.py").write_text("PROMPT = ''\n", encoding="utf-8")
    (app / "main.py").write_text("", encoding="utf-8")
    (app / "windows-bundle.json").write_text(
        json.dumps(
            {
                "backend": {"tag": "v0.1.1-alpha.1"},
                "gui": {"tag": "v0.1.1-alpha.1"},
                "runtime": {"kind": "windows-gtk4-portable"},
            }
        ),
        encoding="utf-8",
    )
    runtime = app / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "lib" / "girepository-1.0").mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"fake")
    for name in ("gtk-4-1.dll", "adwaita-1-0.dll", "gobject-2.0-0.dll"):
        (runtime / "bin" / name).write_bytes(b"fake")
    for name in ("Gtk-4.0.typelib", "Adw-1.typelib"):
        (runtime / "lib" / "girepository-1.0" / name).write_bytes(b"fake")
    return app


def test_validate_portable_gui_accepts_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    app = _make_portable_app(tmp_path)
    installer._validate_portable_gui(app, app / "runtime" / "python.exe")

    (app / "runtime" / "bin" / "gtk-4-1.dll").unlink()
    with pytest.raises(SystemExit):
        installer._validate_portable_gui(app, app / "runtime" / "python.exe")


def test_windows_gui_online_install_from_local_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    app = _make_portable_app(tmp_path / "bundle")
    archive = tmp_path / "KarenBot-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in app.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(tmp_path / "bundle"))

    monkeypatch.setattr(
        installer,
        "_resolve_online_tags",
        lambda *_args, **_kwargs: ("v0.1.1-alpha.1", "v0.1.1-alpha.1"),
    )
    install_dir = tmp_path / "install"
    installer.install(
        frontend="gui",
        install_dir=install_dir,
        source_dir=None,
        archive=None,
        venv=None,
        skip_deps=False,
        create_shortcut=False,
        download_fonts=False,
        online=True,
        release_tag="auto",
        gui_skin="none",
        render_mode="auto",
        windows_gui_asset=str(archive),
    )
    assert (install_dir / "app" / "runtime" / "python.exe").is_file()
    launcher = (install_dir / "launch-gui.cmd").read_text(encoding="utf-8")
    assert "python.exe" in launcher
    assert "-m gui" in launcher
    manifest = json.loads(
        (install_dir / "app" / "modules.json").read_text(encoding="utf-8")
    )
    assert manifest["installer"] == "windows-portable-release"
    assert manifest["components"]["gui"]["tag"] == "v0.1.1-alpha.1"
    assert manifest["components"]["bundle"]["tag"] == "custom"


def test_windows_gui_asset_missing_has_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(
        installer,
        "_latest_release_tag",
        lambda *_args, **_kwargs: ("v0.1.0", "稳定版"),
    )
    monkeypatch.setattr(
        installer,
        "download",
        lambda *_args, **_kwargs: installer.fail("HTTPError: 404"),
    )
    with pytest.raises(SystemExit) as error:
        installer._prepare_windows_gui_app(tmp_path, cancel_event=None)
    message = error.value.message
    assert "预构建包" in message
    assert "docs/windows-gui-package.md" in message


def test_latest_release_tag_uses_repo_and_path(monkeypatch):
    calls = []

    def fake_page(repo, path, **_kwargs):
        calls.append((repo, path))
        return (
            b"<html>redirect</html>",
            "https://github.com/owner/repo/releases/tag/v1.2.3",
        )

    monkeypatch.setattr(installer, "_fetch_github_page", fake_page)
    assert installer._latest_release_tag("owner/repo") == ("v1.2.3", "稳定版")
    assert calls[0] == ("owner/repo", "releases/latest")


def test_latest_release_tag_falls_back_to_atom(monkeypatch):
    calls = []

    def fake_page(repo, path, **_kwargs):
        calls.append((repo, path))
        if path == "releases/latest":
            installer.fail("404")
        return (
            b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            b'<link rel="alternate" href="https://github.com/owner/repo/'
            b'releases/tag/v0.9.0"/></entry></feed>',
            "https://github.com/owner/repo/releases.atom",
        )

    monkeypatch.setattr(installer, "_fetch_github_page", fake_page)
    assert installer._latest_release_tag("owner/repo") == ("v0.9.0", "最新版")
    assert calls == [
        ("owner/repo", "releases/latest"),
        ("owner/repo", "releases.atom"),
    ]


def test_path_problem_rejects_non_ascii_on_windows(monkeypatch):
    import tempfile

    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    bad = Path(tempfile.gettempdir()) / "张三" / "KarenBot"
    problem = installer.path_problem(bad, "安装目录")
    assert problem is not None
    assert "中文" in problem
    assert installer.path_problem(Path(r"C:\Users\Public\KarenBot"), "安装目录") is None


def test_install_rejects_non_ascii_install_dir(tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    with pytest.raises(SystemExit) as error:
        installer.install(
            frontend="webui",
            install_dir=Path(tempfile.gettempdir()) / "中文安装" / "KarenBot",
            source_dir=None,
            archive=None,
            venv=None,
            skip_deps=True,
            create_shortcut=False,
            download_fonts=False,
        )
    assert "非 ASCII" in error.value.message


def test_ensure_writable_dir_reports_permission_error(tmp_path):
    target = tmp_path / "not-a-directory"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        installer.ensure_writable_dir(target)
    assert "不可写" in error.value.message


def test_python_runtime_source_platforms(monkeypatch):
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer.platform, "machine", lambda: "AMD64")
    assert installer._python_runtime_key() == "windows-x64"
    monkeypatch.setattr(installer.platform, "machine", lambda: "ARM64")
    assert installer._python_runtime_key() == "windows-arm64"
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer.platform, "machine", lambda: "aarch64")
    assert installer._python_runtime_key() == "linux-arm64"
    source = installer._python_runtime_source()
    assert source is not None
    assert source["url"].endswith(".tar.gz")
    assert len(source["sha256"]) == 64


def test_ensure_python_runtime_uses_existing_portable(tmp_path, monkeypatch):
    runtime = tmp_path / "python-runtime" / "bin" / "python3"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("", encoding="utf-8")
    monkeypatch.setattr(installer, "_python_is_complete", lambda path: path.is_file())
    assert (
        installer.ensure_python_runtime(tmp_path, frontend="webui")
        == runtime
    )


def test_ensure_python_runtime_downloads_when_missing(tmp_path, monkeypatch):
    source = {
        "url": "https://example.test/python.tar.gz",
        "sha256": "0" * 64,
        "label": "test",
        "key": "linux-x64",
    }
    monkeypatch.setattr(installer, "_find_complete_python", lambda: None)
    monkeypatch.setattr(installer, "_python_runtime_source", lambda: source)
    monkeypatch.setattr(installer, "_python_is_complete", lambda path: path.is_file())

    def fake_download(_url, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, "w:gz") as tar:
            data = b"fake"
            info = tarfile.TarInfo("python/bin/python3")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return destination

    monkeypatch.setattr(installer, "download", fake_download)
    python = installer.ensure_python_runtime(tmp_path, frontend="webui")
    assert python == tmp_path / "python-runtime" / "bin" / "python3"
    assert python.is_file()
    assert not (tmp_path / ".python-runtime-extract").exists()
    assert not list(tmp_path.glob(".python-runtime-*.tar.gz"))


def test_ensure_python_runtime_linux_gui_requires_system(monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", False)
    monkeypatch.setattr(installer, "_find_complete_python", lambda: None)
    with pytest.raises(SystemExit) as error:
        installer.ensure_python_runtime(Path("/tmp/karen-gui-python"), frontend="gui")
    assert "PyGObject" in error.value.message


def test_stage_sink_receives_messages():
    messages: list[str] = []
    installer.set_status_sink(messages.append)
    try:
        installer.stage("自动测速")
    finally:
        installer.set_status_sink(None)
    assert messages == ["自动测速"]


def test_allow_symlinks_still_rejects_escaping_links(tmp_path):
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("python/escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../etc/passwd"
        tar.addfile(info)
    with pytest.raises(SystemExit):
        installer._safe_tar_extract(
            archive, tmp_path / "out", allow_symlinks=True
        )


def test_windows_bundle_asset_url_points_to_installer_repo():
    url = installer._windows_gui_asset_url("v0.1.0")
    assert "CN-BI5UNF/KarenBot-Installer" in url
    assert url.endswith("/KarenBot-Windows-x64.zip")
    assert "/releases/download/v0.1.0/" in url


def test_portable_env_supports_root_python_layout(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    python = runtime / "python.exe"
    python.write_bytes(b"")
    env = installer._portable_env(python)
    assert env["GTK_DATA_PREFIX"] == str(runtime)
    assert env["XDG_DATA_DIRS"] == str(runtime / "share")
    assert env["PATH"] == f"{runtime / 'bin'}{os.pathsep}{runtime}"


def test_portable_env_supports_bin_python_layout(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    python = runtime / "bin" / "python.exe"
    python.write_bytes(b"")
    env = installer._portable_env(python)
    assert env["GTK_DATA_PREFIX"] == str(runtime)
    assert env["PATH"] == str(runtime / "bin")


def test_parse_sha256sums_ignores_comments_and_stars():
    digest = "a" * 64
    parsed = installer.parse_sha256sums(
        f"# checksums\n{digest}  KarenBot-Windows-x64.zip\n"
        f"{digest.upper()} *KarenBot-Personas.zip\n"
    )
    assert parsed["KarenBot-Windows-x64.zip"] == digest
    assert parsed["KarenBot-Personas.zip"] == digest


def test_resolve_pypi_index_fixed_mirror(monkeypatch):
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    installer.set_pypi_mirror_override("auto")
    assert (
        installer.resolve_pypi_index("tsinghua")
        == "https://pypi.tuna.tsinghua.edu.cn/simple"
    )


def test_resolve_pypi_index_auto_picks_highest_score(monkeypatch):
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    monkeypatch.setattr(
        installer,
        "probe_pypi_mirrors",
        lambda **_kwargs: [
            {"ok": True, "mirror": "direct", "url": "https://pypi.org/simple",
             "score": 1.0, "speed": 1000.0, "latency": 0.2},
            {"ok": True, "mirror": "ustc", "url": "https://mirrors.ustc.edu.cn/pypi/simple",
             "score": 9.0, "speed": 9000.0, "latency": 0.05},
        ],
    )
    installer.set_pypi_mirror_override("auto")
    assert (
        installer.resolve_pypi_index("auto")
        == "https://pypi.mirrors.ustc.edu.cn/simple"
    )


def test_ensure_personas_from_local_override(tmp_path, monkeypatch):
    source = tmp_path / "personas"
    persona = source / "demo"
    persona.mkdir(parents=True)
    (persona / "persona_prompt.py").write_text("PROMPT = ''\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setenv("KAREN_PERSONAS_ASSET", str(source))
    installer.ensure_personas(app, tmp_path / "staging")
    assert (app / "personas" / "demo" / "persona_prompt.py").is_file()


def test_ensure_personas_skips_when_present(tmp_path, monkeypatch):
    app = tmp_path / "app"
    persona = app / "personas" / "demo"
    persona.mkdir(parents=True)
    (persona / "persona_prompt.py").write_text("", encoding="utf-8")

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("download should not run")

    monkeypatch.setattr(installer, "download", _unexpected)
    installer.ensure_personas(app, tmp_path / "staging")


def test_failed_upgrade_restores_previous_app(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    old_app = install_dir / "app"
    old_app.mkdir(parents=True)
    (old_app / "previous.txt").write_text("keep me", encoding="utf-8")

    source = tmp_path / "source"
    (source / "backend").mkdir(parents=True)
    (source / "gui").mkdir()
    (source / "new.txt").write_text("new version", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        "[project]\nname='demo'\n", encoding="utf-8"
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated launcher failure")

    monkeypatch.setattr(installer, "_write_launchers", _boom)
    with pytest.raises(RuntimeError):
        installer.install(
            frontend="gui",
            install_dir=install_dir,
            source_dir=source,
            archive=None,
            venv=None,
            skip_deps=True,
            create_shortcut=False,
            download_fonts=False,
        )
    assert (install_dir / "app" / "previous.txt").read_text(encoding="utf-8") == "keep me"
    assert not (install_dir / "app" / "new.txt").exists()
    assert not (install_dir / "app.old").exists()
