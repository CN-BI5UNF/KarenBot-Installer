"""Linux 安装引导器的本地/离线测试。"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import socketserver
import sys
import tarfile
import threading
import time
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
    monkeypatch.setattr(installer, "_font_dir", lambda *_args, **_kwargs: root)

    def fake_download(url, destination, expected_sha256="", **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"font")
        return destination

    monkeypatch.setattr(
        installer,
        "_rank_direct_sources",
        lambda urls, **_kwargs: list(urls),
    )
    monkeypatch.setattr(installer, "download", fake_download)
    installer.install_fonts()
    assert (root / "NotoSansSC-VF.otf").is_file()
    assert (root / "NotoSerifSC-VF.otf").is_file()
    assert (root / "fonts.conf").is_file()


def test_self_test_passes():
    installer.self_test()


def test_rank_direct_sources_orders_by_score(monkeypatch):
    def fake_probe(url, _mirror, **_kwargs):
        if url.endswith("bad.otf"):
            return {"ok": False, "url": url, "score": 0.0}
        return {
            "ok": True,
            "url": url,
            "score": 100.0 if url.endswith("good.otf") else 10.0,
            "latency": 0.1,
        }

    monkeypatch.setattr(installer, "probe_mirror", fake_probe)
    ordered = installer._rank_direct_sources(
        [
            "https://a.test/bad.otf",
            "https://b.test/slow.otf",
            "https://c.test/good.otf",
            "https://c.test/good.otf",
        ]
    )
    assert ordered == [
        "https://c.test/good.otf",
        "https://b.test/slow.otf",
        "https://a.test/bad.otf",
    ]


def test_rank_direct_sources_returns_after_first_good(monkeypatch):
    release_slow = threading.Event()
    fast = "https://fast.test/fast.otf"
    slow = "https://slow.test/slow.otf"

    def fake_probe(url, _mirror, **_kwargs):
        if url == slow:
            release_slow.wait(2.0)
        return {
            "ok": True,
            "url": url,
            "score": 10.0 if url == fast else 1.0,
            "latency": 0.1,
        }

    monkeypatch.setattr(installer, "probe_mirror", fake_probe)
    monkeypatch.setattr(installer, "DIRECT_SOURCE_GOOD_GRACE", 0.05)
    try:
        ordered = installer._rank_direct_sources(
            [fast, slow], timeout=2.0
        )
    finally:
        release_slow.set()
    assert ordered[0] == fast
    assert slow in ordered


def test_rank_direct_sources_empty_and_all_failed(monkeypatch):
    assert installer._rank_direct_sources([]) == []
    monkeypatch.setattr(
        installer,
        "probe_mirror",
        lambda url, _mirror, **_kwargs: {
            "ok": False,
            "url": url,
            "score": 0.0,
        },
    )
    urls = ["https://a.test/a.otf", "https://b.test/b.otf"]
    assert installer._rank_direct_sources(urls) == urls


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
    # 1 秒以内也要保留区分度：80ms 节点应明显优于 800ms 节点。
    assert installer.mirror_score(2048, 0.08) > 2 * installer.mirror_score(
        2048, 0.8
    )


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
    monkeypatch.setattr(installer, "_font_dir", lambda *_args, **_kwargs: font_root)

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


class _PageResponse:
    def __init__(self, data: bytes, url: str) -> None:
        self._data = data
        self._url = url
        self._returned = False

    def read(self, _size: int = -1) -> bytes:
        if self._returned:
            return b""
        self._returned = True
        return self._data

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _race_mirrors(monkeypatch, mirrors=None):
    installer.reset_runtime_caches()
    monkeypatch.delenv("KAREN_INSTALL_MIRROR", raising=False)
    monkeypatch.setattr(
        installer,
        "_MIRROR_CACHE",
        mirrors
        if mirrors is not None
        else [
            {"name": "direct", "kind": "direct", "enabled": True},
            {
                "name": "fast",
                "kind": "prefix",
                "prefix": "https://fast.test",
                "enabled": True,
            },
        ],
    )
    monkeypatch.setattr(installer, "GITHUB_PAGE_STAGGER", 0.05)
    monkeypatch.setattr(installer, "GITHUB_PAGE_TIMEOUT", 0.2)
    monkeypatch.setattr(installer, "GITHUB_PAGE_TOTAL_TIMEOUT", 3.0)


def test_github_page_race_prefers_fast_mirror(monkeypatch):
    _race_mirrors(monkeypatch)
    calls: list[str] = []
    release_direct = threading.Event()

    def fake_urlopen(request, **_kwargs):
        url = request.full_url
        calls.append(url)
        if url.startswith("https://fast.test/"):
            return _PageResponse(
                b"<html>/owner/repo/releases/tag/vFAST</html>", url
            )
        release_direct.wait(2.0)
        return _PageResponse(
            b"<html>/owner/repo/releases/tag/vDIRECT</html>", url
        )

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    try:
        data, final_url = installer._fetch_github_page(
            "owner/repo",
            "releases/latest",
            validator=installer._latest_page_validator("owner/repo"),
        )
    finally:
        release_direct.set()
    assert b"vFAST" in data
    assert "fast.test" in final_url
    assert any("fast.test" in url for url in calls)


def test_github_page_race_skips_invalid_mirror(monkeypatch):
    _race_mirrors(monkeypatch)

    def fake_urlopen(request, **_kwargs):
        url = request.full_url
        if url.startswith("https://fast.test/"):
            return _PageResponse(
                b"<html>/owner/repo/releases/tag/vFAST</html>", url
            )
        return _PageResponse(b"<html>no release tag</html>", url)

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    data, _final_url = installer._fetch_github_page(
        "owner/repo",
        "releases/latest",
        validator=installer._latest_page_validator("owner/repo"),
    )
    assert b"vFAST" in data


def test_github_page_race_direct_404_is_definitive(monkeypatch):
    _race_mirrors(monkeypatch)
    # 直连错误必须先到；把 stagger 拉长，避免调度抖动让镜像提前启动。
    monkeypatch.setattr(installer, "GITHUB_PAGE_STAGGER", 10.0)
    calls: list[str] = []

    def fake_urlopen(request, **_kwargs):
        url = request.full_url
        calls.append(url)
        if url.startswith("https://github.com/"):
            raise installer.urllib.error.HTTPError(
                url, 404, "Not Found", {}, None
            )
        return _PageResponse(
            b"<html>/owner/repo/releases/tag/vFAST</html>", url
        )

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit) as error:
        installer._fetch_github_page(
            "owner/repo",
            "releases/latest",
            validator=installer._latest_page_validator("owner/repo"),
        )
    assert "没有可用的 Release" in error.value.message
    assert all("fast.test" not in url for url in calls)


def test_github_page_atom_404_is_not_definitive(monkeypatch):
    _race_mirrors(monkeypatch)
    feed = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        b'<link rel="alternate" href="https://github.com/owner/repo/'
        b'releases/tag/v0.9.0"/></entry></feed>'
    )

    def fake_urlopen(request, **_kwargs):
        url = request.full_url
        if url.startswith("https://github.com/"):
            raise installer.urllib.error.HTTPError(
                url, 404, "Not Found", {}, None
            )
        return _PageResponse(feed, url)

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    data, _final_url = installer._fetch_github_page(
        "owner/repo",
        "releases.atom",
        validator=installer._atom_page_validator("owner/repo"),
    )
    assert b"v0.9.0" in data


def test_github_page_race_tries_every_mirror(monkeypatch):
    mirrors = [{"name": "direct", "kind": "direct", "enabled": True}]
    for index in range(1, 6):
        mirrors.append(
            {
                "name": f"m{index}",
                "kind": "prefix",
                "prefix": f"https://m{index}.test",
                "enabled": True,
            }
        )
    _race_mirrors(monkeypatch, mirrors)
    monkeypatch.setattr(installer, "GITHUB_PAGE_MAX_PARALLEL", 2)
    calls: list[str] = []

    def fake_urlopen(request, **_kwargs):
        calls.append(request.full_url)
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit):
        installer._fetch_github_page("owner/repo", "releases.atom")
    assert len(calls) == len(mirrors)


def test_github_page_race_starts_all_mirrors_under_deadline(monkeypatch):
    mirrors = [{"name": "direct", "kind": "direct", "enabled": True}]
    for index in range(1, 6):
        mirrors.append(
            {
                "name": f"m{index}",
                "kind": "prefix",
                "prefix": f"https://m{index}.test",
                "enabled": True,
            }
        )
    _race_mirrors(monkeypatch, mirrors)
    monkeypatch.setattr(installer, "GITHUB_PAGE_MAX_PARALLEL", 2)
    monkeypatch.setattr(installer, "GITHUB_PAGE_STAGGER", 0.01)
    monkeypatch.setattr(installer, "GITHUB_PAGE_TIMEOUT", 0.1)
    calls: list[str] = []

    def fake_urlopen(request, **_kwargs):
        calls.append(request.full_url)
        time.sleep(0.2)
        raise TimeoutError("simulated slow mirror")

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit):
        installer._fetch_github_page("owner/repo", "releases.atom")
    assert len(calls) == len(mirrors)


def test_github_page_definitive_grace_keeps_valid_mirror(monkeypatch):
    _race_mirrors(monkeypatch)
    monkeypatch.setattr(installer, "GITHUB_PAGE_STAGGER", 0.0)
    monkeypatch.setattr(installer, "GITHUB_PAGE_DEFINITIVE_GRACE", 1.0)
    direct_error = threading.Event()

    def fake_urlopen(request, **_kwargs):
        url = request.full_url
        if url.startswith("https://github.com/"):
            direct_error.set()
            raise installer.urllib.error.HTTPError(
                url, 404, "Not Found", {}, None
            )
        direct_error.wait(1.0)
        return _PageResponse(
            b"<html>/owner/repo/releases/tag/vFAST</html>", url
        )

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    data, _final_url = installer._fetch_github_page(
        "owner/repo",
        "releases/latest",
        validator=installer._latest_page_validator("owner/repo"),
    )
    assert b"vFAST" in data


def test_github_page_race_reports_all_failures(monkeypatch):
    _race_mirrors(monkeypatch)

    def fake_urlopen(_request, **_kwargs):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit) as error:
        installer._fetch_github_page("owner/repo", "releases.atom")
    assert "无法访问 GitHub" in error.value.message
    assert "direct" in error.value.message
    assert "fast" in error.value.message


def test_atom_page_validator_and_try_parser():
    feed = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        b'<link rel="alternate" href="https://github.com/owner/repo/'
        b'releases/tag/v0.9.0"/></entry></feed>'
    )
    assert installer._try_release_tag_from_atom(feed) == "v0.9.0"
    assert (
        installer._atom_page_validator("owner/repo")(feed, "") is None
    )
    assert (
        installer._atom_page_validator("other/repo")(feed, "") is not None
    )
    assert installer._try_release_tag_from_atom(b"<html>error</html>") is None
    assert (
        installer._atom_page_validator("owner/repo")(
            b"<html>error</html>", ""
        )
        is not None
    )


def test_latest_page_validator_binds_repo():
    page = b"<html>/owner/repo/releases/tag/v1.2.3</html>"
    assert (
        installer._latest_page_validator("owner/repo")(page, "") is None
    )
    assert (
        installer._latest_page_validator("other/repo")(page, "") is not None
    )


def test_release_feed_skips_bad_entries_and_bad_xml():
    long_tag = "v" + "a" * 80
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f'<link rel="alternate" href="https://github.com/owner/repo/'
        f'releases/tag/{long_tag}"/></entry><entry>'
        '<link rel="alternate" href="https://github.com/owner/repo/'
        'releases/tag/v1.0.0"/></entry></feed>'
    ).encode()
    assert installer._try_release_tag_from_atom(feed) == "v1.0.0"
    bad_encoding = b'<?xml version="1.0" encoding="x-unknown"?><feed/>'
    assert installer._try_release_tag_from_atom(bad_encoding) is None


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


# ---------------------------------------------------------------------------
# 计划改进项：缓存、取消、安全校验、Windows 启动器与卸载
# ---------------------------------------------------------------------------


def _make_upgrade_source(tmp_path, *, data_root_supported: bool):
    source = tmp_path / "source"
    (source / "backend").mkdir(parents=True)
    (source / "gui").mkdir()
    (source / "webui").mkdir()
    (source / "pyproject.toml").write_text(
        "[project]\nname='demo'\n", encoding="utf-8"
    )
    if data_root_supported:
        (source / "app_config").mkdir()
        (source / "app_config" / "paths.py").write_text(
            'DATA_ROOT_ENV_VAR = "KAREN_DATA_ROOT"\n', encoding="utf-8"
        )
    return source


def _install_upgrade(source: Path, install_dir: Path, **kwargs):
    # Windows 下源码安装 GUI 会被明确拒绝；用 WebUI 验证同一套数据迁移逻辑。
    frontend = "webui" if installer.IS_WINDOWS else "gui"
    return installer.install(
        frontend=frontend,
        install_dir=install_dir,
        source_dir=source,
        archive=None,
        venv=None,
        skip_deps=True,
        create_shortcut=False,
        download_fonts=False,
        **kwargs,
    )


def _upgrade_launcher(install_dir: Path) -> Path:
    name = "launch-webui.cmd" if installer.IS_WINDOWS else "launch-gui.sh"
    return install_dir / name


def test_upgrade_migrates_legacy_app_data_to_external_root(tmp_path):
    install_dir = tmp_path / "install"
    old_app = install_dir / "app"
    (old_app / "data").mkdir(parents=True)
    (old_app / "data" / "config.yaml").write_text("old", encoding="utf-8")
    (old_app / "personas" / "custom").mkdir(parents=True)
    (old_app / "personas" / "custom" / "persona_prompt.py").write_text(
        "PROMPT = 'custom'\n", encoding="utf-8"
    )
    source = _make_upgrade_source(tmp_path, data_root_supported=True)

    _install_upgrade(source, install_dir)

    target = install_dir / "data" / "instances" / "default"
    assert (target / "config.yaml").read_text(encoding="utf-8") == "old"
    assert not (install_dir / "app" / "data").exists()
    assert (
        install_dir / "app" / "personas" / "custom" / "persona_prompt.py"
    ).is_file()
    launcher = _upgrade_launcher(install_dir).read_text(encoding="utf-8")
    assert installer.DATA_ROOT_ENV in launcher
    assert str(install_dir / "data") in launcher
    webui = (install_dir / "launch-webui.sh").read_text(encoding="utf-8")
    assert installer.DATA_ROOT_ENV in webui
    safe = (install_dir / "launch-gui-safe.sh").read_text(encoding="utf-8")
    assert installer.DATA_ROOT_ENV in safe


def test_install_restores_preserved_personas_from_data(tmp_path):
    install_dir = tmp_path / "install"
    preserved = install_dir / "data" / "personas" / "custom"
    preserved.mkdir(parents=True)
    (preserved / "persona_prompt.py").write_text(
        "PROMPT = 'preserved'\n", encoding="utf-8"
    )
    source = _make_upgrade_source(tmp_path, data_root_supported=True)

    _install_upgrade(source, install_dir)

    assert (
        install_dir / "app" / "personas" / "custom" / "persona_prompt.py"
    ).read_text(encoding="utf-8") == "PROMPT = 'preserved'\n"
    # 人格已经合并进 app/，备份不应留在 data/ 里重复出现。
    assert not (install_dir / "data" / "personas").exists()


def test_upgrade_keeps_app_data_when_backend_lacks_data_root(tmp_path):
    install_dir = tmp_path / "install"
    old_app = install_dir / "app"
    (old_app / "data").mkdir(parents=True)
    (old_app / "data" / "config.yaml").write_text("old", encoding="utf-8")
    source = _make_upgrade_source(tmp_path, data_root_supported=False)

    _install_upgrade(source, install_dir)

    assert (install_dir / "app" / "data" / "config.yaml").read_text(
        encoding="utf-8"
    ) == "old"
    assert not (install_dir / "data").exists()
    launcher = _upgrade_launcher(install_dir).read_text(encoding="utf-8")
    assert installer.DATA_ROOT_ENV not in launcher


def test_upgrade_merges_legacy_data_without_overwriting_external_root(tmp_path):
    install_dir = tmp_path / "install"
    target = install_dir / "data" / "instances" / "default"
    target.mkdir(parents=True)
    (target / "config.yaml").write_text("current", encoding="utf-8")
    old_app = install_dir / "app"
    (old_app / "data").mkdir(parents=True)
    (old_app / "data" / "config.yaml").write_text("old", encoding="utf-8")
    (old_app / "data" / "legacy.txt").write_text("legacy", encoding="utf-8")
    source = _make_upgrade_source(tmp_path, data_root_supported=True)

    _install_upgrade(source, install_dir)

    assert (target / "config.yaml").read_text(encoding="utf-8") == "current"
    assert (target / "legacy.txt").read_text(encoding="utf-8") == "legacy"


def test_fresh_install_downgrade_migrates_external_data(tmp_path):
    install_dir = tmp_path / "install"
    external = install_dir / "data" / "instances" / "default"
    external.mkdir(parents=True)
    (external / "config.yaml").write_text("external", encoding="utf-8")
    source = _make_upgrade_source(tmp_path, data_root_supported=False)

    _install_upgrade(source, install_dir)

    assert (install_dir / "app" / "data" / "config.yaml").read_text(
        encoding="utf-8"
    ) == "external"
    assert not external.exists()


def test_fresh_downgrade_failure_restores_external_data(
    tmp_path, monkeypatch
):
    install_dir = tmp_path / "install"
    external = install_dir / "data" / "instances" / "default"
    external.mkdir(parents=True)
    (external / "config.yaml").write_text("external", encoding="utf-8")
    source = _make_upgrade_source(tmp_path, data_root_supported=False)
    real_stage = installer.stage

    def _fail_at_completion(message: str) -> None:
        if message.startswith("安装完成"):
            raise RuntimeError("simulated post-install failure")
        real_stage(message)

    monkeypatch.setattr(installer, "stage", _fail_at_completion)
    with pytest.raises(RuntimeError):
        _install_upgrade(source, install_dir)

    assert (external / "config.yaml").read_text(encoding="utf-8") == "external"
    assert not (install_dir / "app" / "data").exists()
    assert not (install_dir / "launch-gui.sh").exists()


def test_upgrade_merges_residual_app_old_data(tmp_path):
    install_dir = tmp_path / "install"
    current_app = install_dir / "app"
    (current_app / "backend").mkdir(parents=True)
    (current_app / "gui").mkdir()
    residual = install_dir / "app.old"
    (residual / "data").mkdir(parents=True)
    (residual / "data" / "config.yaml").write_text(
        "residual", encoding="utf-8"
    )
    source = _make_upgrade_source(tmp_path, data_root_supported=True)

    _install_upgrade(source, install_dir)

    target = install_dir / "data" / "instances" / "default"
    assert (target / "config.yaml").read_text(encoding="utf-8") == "residual"
    assert not (install_dir / "app.old").exists()
    assert (install_dir / "app" / "backend").is_dir()


def test_upgrade_restores_orphan_app_old_data(tmp_path):
    install_dir = tmp_path / "install"
    residual = install_dir / "app.old"
    (residual / "data").mkdir(parents=True)
    (residual / "data" / "config.yaml").write_text(
        "orphan", encoding="utf-8"
    )
    source = _make_upgrade_source(tmp_path, data_root_supported=True)

    _install_upgrade(source, install_dir)

    target = install_dir / "data" / "instances" / "default"
    assert (target / "config.yaml").read_text(encoding="utf-8") == "orphan"
    assert not (install_dir / "app.old").exists()
    assert (install_dir / "app" / "backend").is_dir()


def test_upgrade_rollback_restores_migrated_data(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    old_app = install_dir / "app"
    (old_app / "data").mkdir(parents=True)
    (old_app / "data" / "config.yaml").write_text("old", encoding="utf-8")
    old_launcher = install_dir / "launch-gui.sh"
    old_launcher.write_text("#!/bin/sh\n# OLD LAUNCHER\n", encoding="utf-8")
    old_marker = install_dir / installer.INSTALL_MARKER_NAME
    old_marker.write_text('{"old": true}\n', encoding="utf-8")
    source = _make_upgrade_source(tmp_path, data_root_supported=True)
    real_remove = installer._remove_tree

    def _fail_removing_old_app(path, *, strict=False):
        if Path(path).resolve() == (install_dir / "app.old").resolve():
            raise RuntimeError("simulated cleanup failure")
        return real_remove(path, strict=strict)

    monkeypatch.setattr(installer, "_remove_tree", _fail_removing_old_app)
    with pytest.raises(RuntimeError):
        _install_upgrade(source, install_dir)

    assert (install_dir / "app" / "data" / "config.yaml").read_text(
        encoding="utf-8"
    ) == "old"
    assert not (install_dir / "app.old").exists()
    assert not (install_dir / "data" / "instances" / "default").exists()
    assert "OLD LAUNCHER" in old_launcher.read_text(encoding="utf-8")
    assert old_marker.read_text(encoding="utf-8") == '{"old": true}\n'


def test_rank_mirrors_reuses_probe_results(monkeypatch):
    installer.reset_runtime_caches()
    mirrors = [{"name": "cache-test", "kind": "direct"}]
    calls: list[str] = []

    def fake_probe(url, _mirrors=None, **_kwargs):
        calls.append(url)
        return [
            {
                "ok": True,
                "mirror": "cache-test",
                "url": url,
                "speed": 1024.0,
                "latency": 0.2,
                "score": installer.mirror_score(1024.0, 0.2),
            }
        ]

    monkeypatch.setattr(installer, "probe_mirrors", fake_probe)
    first = installer.rank_mirrors("https://example.test/a.tar.gz", mirrors)
    second = installer.rank_mirrors("https://example.test/b.tar.gz", mirrors)
    assert len(calls) == 1
    assert first[0]["url"].endswith("/a.tar.gz")
    assert second[0]["url"].endswith("/b.tar.gz")

    installer.rank_mirrors(
        "https://example.test/c.tar.gz", mirrors, refresh=True
    )
    assert len(calls) == 2


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, _size: int = -1) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _checksum_fixture(monkeypatch, mirror: dict) -> None:
    installer.reset_runtime_caches()
    monkeypatch.setattr(installer, "_checksum_mirrors", lambda: [mirror])
    payload = b"a" * 64 + b"  KarenBot-Windows-x64.zip\n"
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(payload),
    )


def test_fetch_checksums_accepts_direct_manifest(monkeypatch):
    _checksum_fixture(monkeypatch, {"name": "direct", "kind": "direct"})
    checksums = installer.fetch_release_checksums(
        "owner/repo", "v1", require_trusted=True
    )
    assert checksums["KarenBot-Windows-x64.zip"] == "a" * 64


def test_fetch_checksums_rejects_third_party_manifest(monkeypatch):
    _checksum_fixture(
        monkeypatch,
        {
            "name": "proxy",
            "kind": "prefix",
            "prefix": "https://proxy.test/https://github.com",
        },
    )
    checksums = installer.fetch_release_checksums(
        "owner/repo", "v1", require_trusted=True
    )
    assert checksums == {}
    # 明确要求可信清单时，第三方镜像的缓存也不能复用。
    assert (
        installer.fetch_release_checksums(
            "owner/repo", "v1", require_trusted=True
        )
        == {}
    )


def test_fetch_checksums_accepts_third_party_manifest_by_default(monkeypatch):
    _checksum_fixture(
        monkeypatch,
        {
            "name": "proxy",
            "kind": "prefix",
            "prefix": "https://proxy.test/https://github.com",
        },
    )
    checksums = installer.fetch_release_checksums("owner/repo", "v1")
    assert checksums["KarenBot-Windows-x64.zip"] == "a" * 64
    # 默认允许复用镜像清单缓存；严格模式仍会拒绝它。
    assert installer.fetch_release_checksums(
        "owner/repo", "v1"
    )["KarenBot-Windows-x64.zip"] == "a" * 64
    assert (
        installer.fetch_release_checksums(
            "owner/repo", "v1", require_trusted=True
        )
        == {}
    )


def test_extract_archive_honours_preexisting_cancel(tmp_path):
    archive = tmp_path / "asset.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("file.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    event = threading.Event()
    event.set()
    with pytest.raises(installer.InstallCancelled):
        installer.extract_archive(
            archive, tmp_path / "out", cancel_event=event
        )


def test_run_streams_child_output():
    lines: list[str] = []
    installer.set_log_sink(lines.append)
    try:
        installer.run([sys.executable, "-c", "print('stream-marker')"])
    finally:
        installer.set_log_sink(None)
    assert any("stream-marker" in line for line in lines)


def test_run_cancel_terminates_blocking_child():
    event = threading.Event()
    event.set()
    with pytest.raises(installer.InstallCancelled):
        installer.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cancel_event=event,
        )


def test_install_refuses_when_old_app_is_running(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    (install_dir / "app").mkdir(parents=True)
    monkeypatch.setattr(
        installer,
        "_processes_under",
        lambda _root: ["1234|python.exe|/opt/KarenBot/app"],
    )
    with pytest.raises(SystemExit) as error:
        installer.ensure_app_not_running(install_dir)
    assert "仍在运行" in error.value.message


def test_windows_launcher_prefers_pythonw(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    app = tmp_path / "app"
    app.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "python.exe").write_bytes(b"")
    (runtime / "pythonw.exe").write_bytes(b"")
    installer._write_launchers(
        tmp_path,
        app,
        tmp_path / "venv",
        "gui",
        portable_python=runtime / "python.exe",
    )
    normal = (tmp_path / "launch-gui.cmd").read_text(encoding="utf-8")
    safe = (tmp_path / "launch-gui-safe.cmd").read_text(encoding="utf-8")
    webui = (tmp_path / "launch-webui.cmd").read_text(encoding="utf-8")
    assert "pythonw.exe" in normal
    assert 'start "" ' in normal
    assert "/B" not in normal
    assert "exit /b 0" in normal
    assert "python.exe" in safe
    assert "pause" in safe
    assert "python.exe" in webui


def test_windows_prebuilt_gui_launcher_is_windowed(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    app = tmp_path / "app"
    app.mkdir()
    exe = tmp_path / "KarenBot-GUI.exe"
    exe.write_bytes(b"")
    installer._write_launchers(
        tmp_path,
        app,
        tmp_path / "venv",
        "gui",
        prebuilt_exe=exe,
    )
    normal = (tmp_path / "launch-gui.cmd").read_text(encoding="utf-8")
    webui = (tmp_path / "launch-webui.cmd").read_text(encoding="utf-8")
    assert 'start "" ' in normal
    assert "exit /b 0" in normal
    assert 'start "" ' not in webui


def test_windows_shortcut_uses_packaged_icon(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    install_dir = tmp_path / "install"
    assets = install_dir / "app" / "gui" / "assets"
    assets.mkdir(parents=True)
    icon = assets / "karenbot.ico"
    icon.write_bytes(b"ico")
    captured: list[list[str]] = []
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda command, **_kwargs: captured.append(command),
    )
    installer._install_windows_shortcut(install_dir, "gui")
    assert len(captured) == 1
    assert "-ExecutionPolicy" not in captured[0]
    script = captured[0][-1]
    assert "IconLocation" in script
    assert "karenbot.ico" in script
    assert "KarenBot GUI.lnk" in script
    assert "KarenBot GUI (兼容模式).lnk" in script


class _FakeWinregKey:
    def __init__(self, owner: _FakeWinreg) -> None:
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_WRITE = 1
    REG_SZ = 1
    REG_DWORD = 4

    def __init__(self) -> None:
        self.values: dict[str, tuple[int, object]] = {}
        self.deleted: list[str] = []

    def CreateKeyEx(self, _root, key, _reserved, _access):
        self.key = key
        return _FakeWinregKey(self)

    def SetValueEx(self, _handle, name, _reserved, type_, value):
        self.values[name] = (type_, value)

    def DeleteKey(self, _root, key):
        self.deleted.append(key)


def test_prepare_windows_uninstall_launcher_source_mode(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(installer.sys, "frozen", False, raising=False)
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    prepared = installer._prepare_windows_uninstall_launcher(install_dir)
    assert prepared is not None
    command, launcher = prepared
    assert launcher == (
        local
        / "KarenBot"
        / installer.WINDOWS_UNINSTALL_DIRNAME
        / installer.UNINSTALLER_FILENAME
    )
    assert launcher.is_file()
    assert command == [installer.sys.executable, str(launcher)]


def test_prepare_windows_uninstall_launcher_frozen_mode(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    fake_exe = tmp_path / "KarenBot-Installer.exe"
    fake_exe.write_bytes(b"exe")
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer.sys, "executable", str(fake_exe))
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    prepared = installer._prepare_windows_uninstall_launcher(install_dir)
    assert prepared is not None
    command, launcher = prepared
    assert launcher.name == "KarenBot-Installer.exe"
    assert launcher.read_bytes() == b"exe"
    assert command == [str(launcher)]


def test_register_windows_uninstall_writes_hkcu(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "payload.bin").write_bytes(b"x" * 4096)
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    script = tmp_path / installer.UNINSTALLER_FILENAME
    script.write_text("# uninstaller\n", encoding="utf-8")
    icon = tmp_path / "icon.ico"
    icon.write_bytes(b"")
    command = [str(python), str(script)]
    installer._register_windows_uninstall(
        install_dir, command=command, icon=icon
    )
    assert fake.values["DisplayName"] == (fake.REG_SZ, "KarenBot")
    assert fake.values["Publisher"] == (fake.REG_SZ, "CN-BI5UNF")
    interactive = str(fake.values["UninstallString"][1])
    quiet = str(fake.values["QuietUninstallString"][1])
    for part in command:
        assert part in interactive
        assert part in quiet
    assert str(install_dir) in interactive
    assert "--uninstall" in interactive
    # 系统“设置 → 应用”走交互式卸载；只有静默入口带 --non-interactive。
    assert "--non-interactive" not in interactive
    assert quiet == f"{interactive} --non-interactive"
    assert fake.values["NoModify"] == (fake.REG_DWORD, 1)
    assert fake.values["NoRepair"] == (fake.REG_DWORD, 1)
    assert fake.values["EstimatedSize"] == (fake.REG_DWORD, 4)


def test_directory_size_kb_counts_regular_files(tmp_path):
    root = tmp_path / "install"
    (root / "nested").mkdir(parents=True)
    (root / "one.bin").write_bytes(b"x" * 2048)
    (root / "nested" / "two.bin").write_bytes(b"x" * 512)
    assert installer._directory_size_kb(root) == 2
    assert installer._directory_size_kb(tmp_path / "missing") == 0


def test_installer_icon_path_falls_back_to_packaging(tmp_path, monkeypatch):
    missing = tmp_path / "missing.ico"
    monkeypatch.setattr(installer, "_resource_path", lambda _name: missing)
    expected = (
        Path(installer.__file__).resolve().parent
        / "packaging"
        / "karenbot.ico"
    )
    assert installer.installer_icon_path() == expected


def test_unregister_windows_uninstall_deletes_key(monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    installer._unregister_windows_uninstall()
    assert fake.deleted == [installer.WINDOWS_UNINSTALL_KEY]


def test_uninstall_removes_registry_and_uninstaller_copy(
    tmp_path, monkeypatch
):
    install_dir = tmp_path / "install"
    (install_dir / "app" / "backend").mkdir(parents=True)
    (install_dir / "app" / "modules.json").write_text(
        '{"installer": "local-source"}', encoding="utf-8"
    )
    (install_dir / installer.INSTALL_MARKER_NAME).write_text(
        "{}", encoding="utf-8"
    )
    local = tmp_path / "local"
    uninstall_root = (
        local / "KarenBot" / installer.WINDOWS_UNINSTALL_DIRNAME
    )
    uninstall_root.mkdir(parents=True)
    (uninstall_root / installer.UNINSTALLER_FILENAME).write_text(
        "# uninstaller\n", encoding="utf-8"
    )
    fake = _FakeWinreg()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(installer, "ensure_app_not_running", lambda *_a, **_k: None)
    monkeypatch.setattr(installer, "_remove_shortcuts", lambda: None)
    monkeypatch.setattr(installer, "_clean_fonts", lambda *_a, **_k: None)
    monkeypatch.setattr(installer, "_clean_skins", lambda: None)

    installer.uninstall(install_dir)

    assert fake.deleted == [installer.WINDOWS_UNINSTALL_KEY]
    assert not install_dir.exists()
    assert not uninstall_root.exists()


def test_uninstall_removes_install_artifacts(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    (install_dir / "app" / "backend").mkdir(parents=True)
    (install_dir / "app" / "modules.json").write_text(
        '{"installer": "local-source"}', encoding="utf-8"
    )
    (install_dir / "venv").mkdir()
    (install_dir / "data" / "instances" / "default").mkdir(parents=True)
    (install_dir / "data" / "instances" / "default" / "config.yaml").write_text(
        "x: 1\n", encoding="utf-8"
    )
    (install_dir / "launch-gui.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (install_dir / installer.INSTALL_MARKER_NAME).write_text(
        "{}", encoding="utf-8"
    )
    font_root = tmp_path / "fonts"
    font_root.mkdir()
    (font_root / "NotoSansSC-VF.otf").write_text("font", encoding="utf-8")
    (font_root / "fonts.conf").write_text("cfg", encoding="utf-8")
    skin_dir = tmp_path / "config" / "karen-gui"
    skin_dir.mkdir(parents=True)
    (skin_dir / "skin").write_text("karen\n", encoding="utf-8")
    monkeypatch.setattr(installer, "_font_dir", lambda *_args, **_kwargs: font_root)
    monkeypatch.setattr(installer, "_gui_skin_dirs", lambda: [skin_dir])
    monkeypatch.setattr(installer, "_remove_shortcuts", lambda: None)

    installer.uninstall(install_dir)

    assert not install_dir.exists()
    assert not font_root.exists()
    assert not (skin_dir / "skin").exists()


def _make_uninstall_target(install_dir: Path) -> None:
    (install_dir / "app" / "backend").mkdir(parents=True)
    (install_dir / "app" / "modules.json").write_text(
        '{"installer": "local-source"}', encoding="utf-8"
    )
    (install_dir / "app" / "personas" / "custom").mkdir(parents=True)
    (
        install_dir / "app" / "personas" / "custom" / "persona_prompt.py"
    ).write_text("PROMPT = 'custom'\n", encoding="utf-8")
    (install_dir / "venv").mkdir()
    (install_dir / "data" / "instances" / "default").mkdir(parents=True)
    (install_dir / "data" / "instances" / "default" / "config.yaml").write_text(
        "x: 1\n", encoding="utf-8"
    )
    (install_dir / "launch-gui.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (install_dir / installer.INSTALL_MARKER_NAME).write_text(
        "{}", encoding="utf-8"
    )


def _stub_uninstall_side_effects(monkeypatch, font_root: Path, skin_dir: Path):
    monkeypatch.setattr(
        installer, "ensure_app_not_running", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(installer, "_remove_shortcuts", lambda: None)
    monkeypatch.setattr(
        installer, "_font_dir", lambda *_args, **_kwargs: font_root
    )
    monkeypatch.setattr(installer, "_gui_skin_dirs", lambda: [skin_dir])


def test_uninstall_keep_data_preserves_runtime_and_personas(
    tmp_path, monkeypatch
):
    install_dir = tmp_path / "install"
    _make_uninstall_target(install_dir)
    font_root = tmp_path / "fonts"
    skin_dir = tmp_path / "config" / "karen-gui"
    skin_dir.mkdir(parents=True)
    (skin_dir / "skin").write_text("karen\n", encoding="utf-8")
    _stub_uninstall_side_effects(monkeypatch, font_root, skin_dir)

    installer.uninstall(install_dir, keep_data=True)

    assert not (install_dir / "app").exists()
    assert not (install_dir / "venv").exists()
    assert not (install_dir / "launch-gui.sh").exists()
    assert not (install_dir / installer.INSTALL_MARKER_NAME).exists()
    assert (
        install_dir / "data" / "instances" / "default" / "config.yaml"
    ).read_text(encoding="utf-8") == "x: 1\n"
    assert (
        install_dir / "data" / "personas" / "custom" / "persona_prompt.py"
    ).read_text(encoding="utf-8") == "PROMPT = 'custom'\n"
    # 保留数据的卸载不应清理主题偏好，方便重装后沿用。
    assert (skin_dir / "skin").exists()


def test_uninstall_keep_data_migrates_legacy_app_data(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    _make_uninstall_target(install_dir)
    legacy = install_dir / "app" / "data"
    (legacy / "memory" / "default").mkdir(parents=True)
    (legacy / "config.yaml").write_text("legacy", encoding="utf-8")
    (legacy / "memory" / "default" / "chat.json").write_text(
        "[]", encoding="utf-8"
    )
    font_root = tmp_path / "fonts"
    skin_dir = tmp_path / "config" / "karen-gui"
    _stub_uninstall_side_effects(monkeypatch, font_root, skin_dir)

    installer.uninstall(install_dir, keep_data=True)

    target = install_dir / "data" / "instances" / "default"
    # 现有外部数据优先，旧版 app/data 只补充缺失文件。
    assert (target / "config.yaml").read_text(encoding="utf-8") == "x: 1\n"
    assert (target / "memory" / "default" / "chat.json").is_file()
    assert not (install_dir / "app").exists()


def test_uninstall_keep_data_refuses_data_symlink_into_app(
    tmp_path, monkeypatch
):
    install_dir = tmp_path / "install"
    _make_uninstall_target(install_dir)
    installer._remove_tree(install_dir / "data", strict=True)
    (install_dir / "app" / "data").mkdir()
    (install_dir / "app" / "data" / "config.yaml").write_text(
        "legacy", encoding="utf-8"
    )
    try:
        (install_dir / "data").symlink_to(
            install_dir / "app" / "data", target_is_directory=True
        )
    except OSError:
        pytest.skip("当前平台不允许创建目录符号链接")
    font_root = tmp_path / "fonts"
    skin_dir = tmp_path / "config" / "karen-gui"
    _stub_uninstall_side_effects(monkeypatch, font_root, skin_dir)

    with pytest.raises(SystemExit) as error:
        installer.uninstall(install_dir, keep_data=True)

    assert "符号链接" in error.value.message
    # 必须原样保留，不能先删 app/ 再发现数据在 app/ 里。
    assert (
        install_dir / "app" / "data" / "config.yaml"
    ).read_text(encoding="utf-8") == "legacy"


def test_uninstall_cli_passes_keep_data(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_uninstall(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return path

    monkeypatch.setattr(installer, "uninstall", fake_uninstall)
    assert (
        installer.main(
            ["--uninstall", "--dir", str(tmp_path), "--keep-data"]
        )
        == 0
    )
    assert captured["path"] == tmp_path
    assert captured["keep_data"] is True


def test_restore_preserved_personas_merges_without_overwriting(tmp_path):
    install_dir = tmp_path / "install"
    source = install_dir / "data" / "personas"
    (source / "custom").mkdir(parents=True)
    (source / "custom" / "persona_prompt.py").write_text(
        "custom", encoding="utf-8"
    )
    (source / "builtin").mkdir()
    (source / "builtin" / "persona_prompt.py").write_text(
        "old", encoding="utf-8"
    )
    target_app = install_dir / "app"
    (target_app / "personas" / "builtin").mkdir(parents=True)
    (target_app / "personas" / "builtin" / "persona_prompt.py").write_text(
        "new", encoding="utf-8"
    )

    installer._restore_preserved_personas(install_dir, target_app)

    assert (
        target_app / "personas" / "custom" / "persona_prompt.py"
    ).read_text(encoding="utf-8") == "custom"
    assert (
        target_app / "personas" / "builtin" / "persona_prompt.py"
    ).read_text(encoding="utf-8") == "new"
    # 备份要保留到整套安装流程结束后才能删除。
    assert source.is_dir()
    installer._discard_preserved_personas(install_dir)
    assert not source.exists()


def test_uninstall_refuses_unknown_directory(tmp_path):
    target = tmp_path / "not-karenbot"
    target.mkdir()
    with pytest.raises(SystemExit) as error:
        installer.uninstall(target)
    assert "不是 KarenBot 安装目录" in error.value.message


def test_prepare_windows_gui_requires_trusted_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(
        installer,
        "_windows_bundle_tag",
        lambda *_args, **_kwargs: ("v1.0.0", "稳定版"),
    )
    monkeypatch.setattr(
        installer,
        "fetch_release_checksums",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.delenv(installer.UNVERIFIED_ENV, raising=False)
    with pytest.raises(SystemExit) as error:
        installer._prepare_windows_gui_app(tmp_path, cancel_event=None)
    assert "无法验证" in error.value.message
    assert installer.UNVERIFIED_ENV in error.value.message


def test_prepare_windows_gui_allows_explicit_unverified_opt_in(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(
        installer,
        "_windows_bundle_tag",
        lambda *_args, **_kwargs: ("v1.0.0", "稳定版"),
    )
    monkeypatch.setattr(
        installer,
        "fetch_release_checksums",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setenv(installer.UNVERIFIED_ENV, "1")
    monkeypatch.setattr(
        installer,
        "download",
        lambda *_args, **_kwargs: installer.fail("simulated-download-failure"),
    )
    with pytest.raises(SystemExit) as error:
        installer._prepare_windows_gui_app(tmp_path, cancel_event=None)
    assert "simulated-download-failure" in error.value.message


def test_prepare_windows_gui_checksum_trust_follows_require_verified(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(
        installer,
        "_windows_bundle_tag",
        lambda *_args, **_kwargs: ("v1.0.0", "稳定版"),
    )
    seen: list[bool] = []

    def fake_fetch(*_args, **kwargs):
        seen.append(bool(kwargs.get("require_trusted")))
        return {}

    monkeypatch.setattr(installer, "fetch_release_checksums", fake_fetch)
    for name in (
        installer.UNVERIFIED_ENV,
        "KAREN_REQUIRE_VERIFIED",
        "KAREN_WINDOWS_GUI_SHA256",
        installer.WINDOWS_GUI_ASSET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit):
        installer._prepare_windows_gui_app(tmp_path, cancel_event=None)
    monkeypatch.setenv("KAREN_REQUIRE_VERIFIED", "1")
    with pytest.raises(SystemExit):
        installer._prepare_windows_gui_app(tmp_path, cancel_event=None)
    assert seen == [False, True]


def test_prepare_windows_gui_uses_mirror_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setattr(
        installer,
        "_windows_bundle_tag",
        lambda *_args, **_kwargs: ("v1.0.0", "稳定版"),
    )
    digest = "a" * 64
    monkeypatch.setattr(
        installer,
        "fetch_release_checksums",
        lambda *_args, **_kwargs: {installer.windows_gui_asset_name(): digest},
    )
    captured: dict[str, str] = {}

    def fake_download(_url, _path, **kwargs):
        captured["expected"] = str(kwargs.get("expected_sha256") or "")
        raise installer.InstallFailure("simulated-download-stop")

    monkeypatch.setattr(installer, "download", fake_download)
    for name in (
        installer.UNVERIFIED_ENV,
        "KAREN_REQUIRE_VERIFIED",
        "KAREN_WINDOWS_GUI_SHA256",
        installer.WINDOWS_GUI_ASSET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit):
        installer._prepare_windows_gui_app(tmp_path, cancel_event=None)
    assert captured["expected"] == digest


def test_ensure_personas_checksum_trust_follows_require_verified(
    tmp_path, monkeypatch
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    seen: list[bool] = []

    def fake_fetch(*_args, **kwargs):
        seen.append(bool(kwargs.get("require_trusted")))
        return {}

    monkeypatch.setattr(installer, "_windows_bundle_tag", lambda *_a, **_k: ("v1.0.0", "稳定版"))
    monkeypatch.setattr(installer, "fetch_release_checksums", fake_fetch)
    monkeypatch.setattr(installer, "_direct_ranking", lambda *_a, **_k: None)
    monkeypatch.setattr(
        installer,
        "download",
        lambda *_a, **_k: installer.fail("simulated-download-stop"),
    )
    for name in (
        installer.UNVERIFIED_ENV,
        "KAREN_REQUIRE_VERIFIED",
        "KAREN_PERSONAS_SHA256",
        installer.PERSONAS_ASSET_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit):
        installer.ensure_personas(app_root, staging)
    monkeypatch.setenv("KAREN_REQUIRE_VERIFIED", "1")
    with pytest.raises(SystemExit):
        installer.ensure_personas(app_root, staging)
    assert seen == [False, True]


def test_prioritize_direct_moves_working_direct_first():
    candidates = [
        {"mirror": "proxy", "url": "https://proxy.test/a"},
        {"mirror": "direct", "url": "https://github.com/a"},
        {"mirror": "other", "url": "https://other.test/a"},
    ]
    ordered = installer._prioritize_direct(candidates)
    assert [item["mirror"] for item in ordered] == [
        "direct",
        "proxy",
        "other",
    ]
    # 探测失败时 direct 不在列表里，保持原顺序。
    assert installer._prioritize_direct(candidates[::2]) == candidates[::2]


class _DownloadResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers: dict[str, str] = {}

    def read(self, _size: int = -1) -> bytes:
        payload, self.payload = self.payload, b""
        return payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_download_prefers_direct_candidate(monkeypatch, tmp_path):
    targets: list[str] = []
    candidates = [
        {"mirror": "proxy", "url": "https://proxy.test/asset"},
        {"mirror": "direct", "url": "https://github.com/asset"},
    ]
    monkeypatch.setattr(
        installer, "rank_mirrors", lambda *_args, **_kwargs: candidates
    )

    def fake_request(url, **_kwargs):
        targets.append(url)
        return url

    monkeypatch.setattr(installer, "_request", fake_request)
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(b"payload"),
    )
    installer.download(
        "https://github.com/asset",
        tmp_path / "asset.bin",
        prefer_direct=True,
    )
    assert targets[0] == "https://github.com/asset"


def test_env_sha256_rejects_malformed_values(monkeypatch):
    monkeypatch.setenv("KAREN_BACKEND_SHA256", "not-a-hash")
    with pytest.raises(SystemExit) as error:
        installer._env_sha256("KAREN_BACKEND_SHA256")
    assert "SHA256" in error.value.message


def test_env_sha256_accepts_lowercase_hash(monkeypatch):
    digest = "a" * 64
    monkeypatch.setenv("KAREN_BACKEND_SHA256", digest.upper())
    assert installer._env_sha256("KAREN_BACKEND_SHA256") == digest


def test_require_verified_blocks_unhashed_online_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(
        installer,
        "_resolve_online_tags",
        lambda *_args, **_kwargs: ("v1.0.0", "v1.0.0"),
    )
    monkeypatch.setenv("KAREN_REQUIRE_VERIFIED", "1")
    monkeypatch.delenv("KAREN_BACKEND_SHA256", raising=False)
    monkeypatch.delenv("KAREN_WEBUI_SHA256", raising=False)
    with pytest.raises(SystemExit) as error:
        installer._prepare_online_app(
            tmp_path,
            frontend="webui",
            release_tag="auto",
            cancel_event=None,
        )
    assert "KAREN_REQUIRE_VERIFIED" in error.value.message


def test_online_sources_use_explicit_hashes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        installer,
        "_resolve_online_tags",
        lambda *_args, **_kwargs: ("v1.0.0", "v1.0.0"),
    )
    backend_hash = "b" * 64
    frontend_hash = "c" * 64
    monkeypatch.setenv("KAREN_BACKEND_SHA256", backend_hash)
    monkeypatch.setenv("KAREN_WEBUI_SHA256", frontend_hash)
    captured: list[dict] = []

    def fake_download(url, destination, **kwargs):
        captured.append({"url": url, **kwargs})
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake")
        return destination

    def fake_extract(*_args, **_kwargs):
        installer.fail("stop-after-capture")

    monkeypatch.setattr(installer, "download", fake_download)
    monkeypatch.setattr(installer, "extract_archive", fake_extract)
    with pytest.raises(SystemExit):
        installer._prepare_online_app(
            tmp_path,
            frontend="webui",
            release_tag="auto",
            cancel_event=None,
        )
    assert captured[0]["expected_sha256"] == backend_hash
    assert captured[0]["ranked"] is None
    assert captured[1]["expected_sha256"] == frontend_hash
    assert captured[1]["ranked"] is None


# ---------------------------------------------------------------------------
# 审计修复：venv 自愈、字体路径、测速校验、断点续传、无哈希源码策略
# ---------------------------------------------------------------------------


def test_create_venv_rebuilds_broken_managed_env(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("broken", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        installer,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )
    installer._create_venv(
        venv,
        frontend="webui",
        base_python=Path(sys.executable),
        managed=True,
    )
    assert calls
    assert calls[0][:3] == [sys.executable, "-m", "venv"]
    assert not (venv / "bin" / "python").exists()


def test_create_venv_does_not_delete_explicit_env(tmp_path, monkeypatch):
    venv = tmp_path / "custom-venv"
    venv.mkdir()
    marker = venv / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(installer, "_venv_is_usable", lambda _venv: False)
    monkeypatch.setattr(
        installer,
        "run",
        lambda *_args, **_kwargs: pytest.fail("不应重建显式 venv"),
    )
    installer._create_venv(
        venv,
        frontend="webui",
        base_python=Path(sys.executable),
        managed=False,
    )
    assert marker.read_text(encoding="utf-8") == "keep"


def test_font_dir_falls_back_for_non_ascii_localappdata(monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\张三\AppData\Local")
    install_dir = Path(r"C:\KarenBot")
    assert installer._font_dir(install_dir) == install_dir / "fonts"
    assert installer._font_dir() == (
        Path(installer._ascii_install_suggestion()) / "fonts"
    )


def test_probe_payload_rejects_html_archive():
    problem = installer._probe_payload_problem(
        "https://mirror.test/KarenBot-Windows-x64.zip",
        b"<!doctype html><html>rate limit</html>",
        "text/html",
    )
    assert problem and "zip" in problem


def test_probe_payload_accepts_gzip_archive():
    assert (
        installer._probe_payload_problem(
            "https://mirror.test/backend.tar.gz",
            b"\x1f\x8b\x08" + b"x" * 300,
            "text/html",
        )
        is None
    )


def test_probe_payload_rejects_plain_text_error():
    problem = installer._probe_payload_problem(
        "https://mirror.test/asset.bin",
        b"Too Many Requests" * 40,
        "text/plain",
    )
    assert problem and "错误文本" in problem


def test_default_probe_url_matches_windows_asset(monkeypatch):
    monkeypatch.setattr(installer, "IS_WINDOWS", True)
    windows = installer.default_probe_url("gui")
    assert "/releases/latest/download/" in windows
    assert installer.windows_gui_asset_name() in windows
    assert installer.default_probe_url("webui") == installer.DEFAULT_PROBE_URL


def _capture_online_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(
        installer,
        "_resolve_online_tags",
        lambda *_args, **_kwargs: ("v1.0.0", "v1.0.0"),
    )
    captured: list[dict] = []

    def fake_download(url, destination, **kwargs):
        captured.append({"url": url, **kwargs})
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake")
        return destination

    monkeypatch.setattr(installer, "download", fake_download)
    monkeypatch.setattr(
        installer,
        "extract_archive",
        lambda *_args, **_kwargs: installer.fail("stop-after-capture"),
    )
    with pytest.raises(SystemExit):
        installer._prepare_online_app(
            tmp_path,
            frontend="webui",
            release_tag="auto",
            cancel_event=None,
        )
    return captured


def test_unverified_online_sources_default_to_direct(monkeypatch, tmp_path):
    monkeypatch.delenv(installer.UNVERIFIED_ENV, raising=False)
    monkeypatch.delenv("KAREN_BACKEND_SHA256", raising=False)
    monkeypatch.delenv("KAREN_WEBUI_SHA256", raising=False)
    captured = _capture_online_sources(monkeypatch, tmp_path)
    assert captured[0]["ranked"][0]["mirror"] == "direct"
    assert captured[1]["ranked"][0]["mirror"] == "direct"


def test_unverified_online_sources_use_mirrors_after_opt_in(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(installer.UNVERIFIED_ENV, "1")
    monkeypatch.delenv("KAREN_BACKEND_SHA256", raising=False)
    monkeypatch.delenv("KAREN_WEBUI_SHA256", raising=False)
    captured = _capture_online_sources(monkeypatch, tmp_path)
    assert captured[0]["ranked"] is None
    assert captured[1]["ranked"] is None


class _RangeResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 206,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self.status = status
        self.headers = headers or {}

    def read(self, _size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_download_resumes_partial_file(monkeypatch, tmp_path):
    payload = b"0123456789abcdef"
    destination = tmp_path / "asset.bin"
    part = destination.with_name(destination.name + ".part")
    part.write_bytes(payload[:8])
    requests: list[dict] = []

    def fake_request(url, **kwargs):
        requests.append(kwargs)
        return url

    monkeypatch.setattr(installer, "_request", fake_request)
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _RangeResponse(
            [payload[8:]],
            status=206,
            headers={
                "Content-Range": f"bytes 8-{len(payload) - 1}/{len(payload)}",
                "Content-Length": str(len(payload) - 8),
            },
        ),
    )
    installer.download(
        "https://mirror.test/asset.bin",
        destination,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        ranked=installer._direct_ranking("https://mirror.test/asset.bin"),
    )
    assert destination.read_bytes() == payload
    assert requests and requests[0]["headers"] == {"Range": "bytes=8-"}
    assert not part.exists()


def test_download_keeps_partial_file_for_next_mirror(monkeypatch, tmp_path):
    destination = tmp_path / "asset.bin"
    monkeypatch.setattr(
        installer,
        "_request",
        lambda url, **_kwargs: url,
    )
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _RangeResponse(
            [b"01234567"],
            status=200,
            headers={"Content-Length": "16"},
        ),
    )
    with pytest.raises(SystemExit):
        installer.download(
            "https://mirror.test/asset.bin",
            destination,
            ranked=installer._direct_ranking(
                "https://mirror.test/asset.bin"
            ),
        )
    part = destination.with_name(destination.name + ".part")
    assert part.read_bytes() == b"01234567"


def test_download_restarts_same_mirror_after_416(monkeypatch, tmp_path):
    payload = b"0123456789abcdef"
    destination = tmp_path / "asset.bin"
    part = destination.with_name(destination.name + ".part")
    part.write_bytes(b"x" * 32)
    requests: list[dict | None] = []

    def fake_request(url, **kwargs):
        requests.append(kwargs.get("headers"))
        return url

    monkeypatch.setattr(installer, "_request", fake_request)
    calls = {"count": 0}

    def fake_urlopen(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise installer.urllib.error.HTTPError(
                "https://mirror.test/asset.bin",
                416,
                "Range Not Satisfiable",
                {},
                None,
            )
        return _StreamingResponse(
            [payload], headers={"Content-Length": str(len(payload))}
        )

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    installer.download(
        "https://mirror.test/asset.bin",
        destination,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        ranked=installer._direct_ranking(
            "https://mirror.test/asset.bin"
        ),
    )
    assert destination.read_bytes() == payload
    assert requests[0] == {"Range": "bytes=32-"}
    assert requests[1] is None


class _StreamingResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        error_at: int | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self.status = status
        self.headers = headers or {}
        self._error_at = error_at
        self._index = 0

    def read(self, _size: int = -1) -> bytes:
        if self._error_at is not None and self._index == self._error_at:
            raise TimeoutError("simulated connection reset")
        self._index += 1
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_download_discards_part_when_switching_mirrors(monkeypatch, tmp_path):
    payload = b"0123456789abcdef"
    destination = tmp_path / "asset.bin"
    requests: list[tuple[str, dict | None]] = []

    def fake_request(url, **kwargs):
        requests.append((url, kwargs.get("headers")))
        return url

    monkeypatch.setattr(installer, "_request", fake_request)
    calls = {"count": 0}

    def fake_urlopen(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _StreamingResponse(
                [payload[:8]],
                headers={"Content-Length": str(len(payload))},
                error_at=1,
            )
        return _StreamingResponse(
            [payload], headers={"Content-Length": str(len(payload))}
        )

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    installer.download(
        "https://mirror-a.test/asset.bin",
        destination,
        ranked=[
            {
                "mirror": "a",
                "url": "https://mirror-a.test/asset.bin",
            },
            {
                "mirror": "b",
                "url": "https://mirror-b.test/asset.bin",
            },
        ],
    )
    assert destination.read_bytes() == payload
    assert requests[0][1] is None
    assert requests[1][1] is None
    assert not destination.with_name(destination.name + ".part").exists()
    assert not destination.with_name(
        destination.name + ".part.origin"
    ).exists()


def test_download_rejects_json_error_page(monkeypatch, tmp_path):
    destination = tmp_path / "backend.tar.gz"

    def fake_request(url, **_kwargs):
        return url

    monkeypatch.setattr(installer, "_request", fake_request)
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _StreamingResponse(
            [b'{"code": 429, "msg": "rate limit"}'],
            headers={
                "Content-Type": "application/json",
                "Content-Length": "35",
            },
        ),
    )
    with pytest.raises(SystemExit) as error:
        installer.download(
            "https://mirror.test/backend.tar.gz",
            destination,
            ranked=installer._direct_ranking(
                "https://mirror.test/backend.tar.gz"
            ),
        )
    assert "tar.gz" in error.value.message
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".part").exists()


def test_windows_process_scan_uses_ctypes_without_powershell():
    source = Path(installer.__file__).read_text(encoding="utf-8")
    assert "CreateToolhelp32Snapshot" in source
    assert "QueryFullProcessImageNameW" in source
    assert "-ExecutionPolicy" not in source
