"""Tkinter 安装器的线程/队列/界面状态烟测。

默认测试矩阵没有 DISPLAY，会自动跳过；构建 workflow 用 xvfb-run 执行。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

import gui_install  # noqa: E402
import karen_install  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name != "nt" and not os.environ.get("DISPLAY"),
    reason="GUI flow test needs a display (use xvfb-run)",
)


def _pump(root, predicate, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _make_app(tmp_path: Path, monkeypatch):
    messages: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        gui_install.messagebox,
        "showinfo",
        lambda *args, **kwargs: messages.append(("info", args)),
    )
    monkeypatch.setattr(
        gui_install.messagebox,
        "showerror",
        lambda *args, **kwargs: messages.append(("error", args)),
    )
    monkeypatch.setattr(
        gui_install.messagebox,
        "showwarning",
        lambda *args, **kwargs: messages.append(("warning", args)),
    )
    monkeypatch.setattr(
        gui_install.messagebox,
        "askyesno",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        gui_install.messagebox,
        "askyesnocancel",
        lambda *args, **kwargs: False,
    )
    source = tmp_path / "source"
    (source / "backend").mkdir(parents=True)
    (source / "gui").mkdir()
    root = tk.Tk()
    root.withdraw()
    app = gui_install.InstallerApp(root)
    app.source_mode.set("source")
    app.source_path.set(str(source))
    app.install_dir.set(str(tmp_path / "install"))
    app.download_fonts.set(False)
    app.create_shortcut.set(False)
    app.gui_skin.set("karen")
    app.render_mode.set("auto")
    app._update_source_state()
    app._update_appearance_state()
    if os.name == "nt":
        # Windows 源码安装只允许 WebUI；GUI 需要预构建 GTK4 包。
        app.frontend.set("webui")
    return root, app, messages


def test_gui_reports_success(monkeypatch, tmp_path):
    root, app, messages = _make_app(tmp_path, monkeypatch)
    captured: dict = {}

    def fake_install(**kwargs):
        captured.update(kwargs)
        karen_install.log("fake install")
        return kwargs["install_dir"]

    monkeypatch.setattr(karen_install, "install", fake_install)
    app._start_install()
    try:
        assert _pump(root, lambda: app.status.get() == "安装完成")
        assert app._installed_dir == tmp_path / "install"
        assert messages and messages[-1][0] == "info"
        assert captured["gui_skin"] == "karen"
        assert captured["render_mode"] == "auto"
        assert captured["allow_unverified"] is False
    finally:
        root.destroy()


def test_gui_reports_failure(monkeypatch, tmp_path):
    root, app, messages = _make_app(tmp_path, monkeypatch)

    def fake_install(**_kwargs):
        raise karen_install.InstallFailure("boom")

    monkeypatch.setattr(karen_install, "install", fake_install)
    app._start_install()
    try:
        assert _pump(root, lambda: app.status.get() == "安装失败")
        assert messages and messages[-1][0] == "error"
    finally:
        root.destroy()


def test_mirror_dialog_renders_results(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    try:
        app._open_mirror_dialog()
        app._show_mirrors(
            [
                {
                    "mirror": "fast",
                    "ok": True,
                    "speed": 2048.0,
                    "latency": 0.1,
                },
                {
                    "mirror": "bad",
                    "ok": False,
                    "error": "timeout",
                    "speed": 0.0,
                    "latency": 999.0,
                },
            ]
        )
        rows = app._mirror_tree.get_children()
        assert len(rows) == 2
        assert app._mirror_tree.item(rows[0], "values")[0] == "fast"
        app._close_mirror_dialog()
        app._open_mirror_dialog()
        assert app._mirror_tree is not None
    finally:
        root.destroy()


def test_gui_updates_progress_step(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    try:
        app._on_stage("自动测速并下载 backend 核心包…")
        assert app.progress_step.get() == "自动测速并下载 backend 核心包…"
        assert app.status.get() == "自动测速并下载 backend 核心包…"
    finally:
        root.destroy()


def test_gui_rejects_non_ascii_or_invalid_path(monkeypatch, tmp_path):
    root, app, messages = _make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gui_install.core,
        "path_problem",
        lambda path, label="路径": f"{label}包含中文：{path}",
    )
    try:
        app._start_install()
        assert messages and messages[-1][0] == "error"
        assert app._worker is None
        assert app._progress_window is None
    finally:
        root.destroy()


def test_gui_locks_all_config_widgets_while_running(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    try:
        app._set_running(True)
        assert str(app.install_entry.cget("state")) == "disabled"
        assert str(app.github_mirror_box.cget("state")) == "disabled"
        assert str(app.source_radios[0].cget("state")) == "disabled"
        assert str(app.frontend_radios[0].cget("state")) == "disabled"
        app._set_running(False)
        assert str(app.install_entry.cget("state")) == "normal"
        assert str(app.github_mirror_box.cget("state")) == "readonly"
        assert str(app.source_entry.cget("state")) == "normal"
        assert str(app.frontend_radios[0].cget("state")) == "normal"
    finally:
        root.destroy()


def test_gui_mirror_dialog_can_pin_available_mirror(monkeypatch, tmp_path):
    root, app, messages = _make_app(tmp_path, monkeypatch)
    try:
        app._open_mirror_dialog()
        app._show_mirrors(
            [
                {"mirror": "fast", "ok": True, "speed": 2048.0, "latency": 0.1},
                {"mirror": "bad", "ok": False, "error": "timeout"},
            ]
        )
        rows = app._mirror_tree.get_children()
        app._mirror_tree.selection_set(rows[0])
        app._apply_selected_mirror()
        assert app.github_mirror.get() == "fast"
        app._mirror_tree.selection_set(rows[1])
        app._apply_selected_mirror()
        assert app.github_mirror.get() == "fast"
        assert messages and messages[-1][0] == "error"
    finally:
        root.destroy()


def test_gui_progress_speed_uses_window_not_first_chunk(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    times = iter([100.0, 100.05, 101.0])
    monkeypatch.setattr(gui_install.time, "monotonic", lambda: next(times))
    try:
        app._update_progress(0, 1_000_000)
        app._update_progress(262_144, 1_000_000)
        assert "MB/s" not in app.dialog_status.get()
        app._update_progress(800_000, 1_000_000)
        assert "MB/s" in app.dialog_status.get()
    finally:
        root.destroy()


def test_gui_uninstall_button_tracks_install_marker(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    install_dir = tmp_path / "install"
    (install_dir / "app").mkdir(parents=True)
    (install_dir / "app" / "modules.json").write_text(
        '{"installer": "local-source"}', encoding="utf-8"
    )
    try:
        app.install_dir.set(str(install_dir))
        app._update_uninstall_state()
        assert str(app.uninstall_button.cget("state")) == "normal"
        app._set_running(True)
        assert str(app.uninstall_button.cget("state")) == "disabled"
    finally:
        root.destroy()


def test_gui_reports_uninstall_success(monkeypatch, tmp_path):
    root, app, messages = _make_app(tmp_path, monkeypatch)
    install_dir = tmp_path / "install"
    (install_dir / "app").mkdir(parents=True)
    (install_dir / "app" / "modules.json").write_text(
        '{"installer": "local-source"}', encoding="utf-8"
    )
    monkeypatch.setattr(
        karen_install,
        "uninstall",
        lambda path, **_kwargs: path,
    )
    try:
        app.install_dir.set(str(install_dir))
        app._start_uninstall()
        assert _pump(root, lambda: app.status.get() == "已卸载")
        assert messages and messages[-1][0] == "info"
        assert app._installed_dir is None
    finally:
        root.destroy()


def test_gui_existing_install_enables_launch_buttons(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    install_dir = tmp_path / "install"
    (install_dir / "app").mkdir(parents=True)
    (install_dir / ".karen-installer.json").write_text(
        '{"frontend": "webui", "installer": "online-release"}',
        encoding="utf-8",
    )
    try:
        app.install_dir.set(str(install_dir))
        app._update_uninstall_state()
        assert str(app.open_button.cget("state")) == "normal"
        assert str(app.launch_button.cget("state")) == "normal"
        assert str(app.launch_safe_button.cget("state")) == "normal"
        assert app._installed_dir == install_dir
        assert app._installed_frontend == "webui"
    finally:
        root.destroy()


def test_gui_log_copy_and_save(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    target = tmp_path / "saved.log"
    monkeypatch.setattr(
        gui_install.filedialog,
        "asksaveasfilename",
        lambda **_kwargs: str(target),
    )
    try:
        app._log_line("copy-save-marker")
        assert "copy-save-marker" in app._log_text_value()
        app._copy_log()
        assert "copy-save-marker" in root.clipboard_get()
        app._save_log()
        assert "copy-save-marker" in target.read_text(encoding="utf-8")
    finally:
        root.destroy()


def test_gui_self_test_entrypoint(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(gui_install.core, "self_test", lambda: calls.append(True))
    assert gui_install.main(["--self-test"]) == 0
    assert calls == [True]


def test_gui_self_test_entrypoint_failure(monkeypatch):
    def _boom() -> None:
        raise RuntimeError("self-test boom")

    monkeypatch.setattr(gui_install.core, "self_test", _boom)
    assert gui_install.main(["--self-test"]) == 1


def test_gui_progress_dialog_is_non_modal(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    try:
        app._open_progress_dialog()
        assert app._progress_window is not None
        assert app._progress_window.grab_current() is None
        app._close_progress_dialog()
    finally:
        root.destroy()


def test_gui_progress_dialog_uses_uninstall_wording(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    try:
        app._open_progress_dialog(
            title="正在卸载 KarenBot", cancel_text="取消卸载"
        )
        assert app._progress_window is not None
        assert app._progress_window.title() == "正在卸载 KarenBot"
        def _texts(widget) -> list[str]:
            found: list[str] = []
            if "text" in widget.keys():
                found.append(str(widget.cget("text")))
            for child in widget.winfo_children():
                found.extend(_texts(child))
            return found

        assert "取消卸载" in _texts(app._progress_window)
        app._close_progress_dialog()
    finally:
        root.destroy()


def test_gui_show_log_closes_transient_progress_dialog(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    try:
        app._open_progress_dialog()
        assert app._progress_window is not None
        app._show_log_tab()
        assert app._progress_window is None
    finally:
        root.destroy()


def test_gui_close_waits_for_worker_rollback(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)

    class _Worker:
        alive = True

        def is_alive(self):
            return self.alive

    worker = _Worker()
    app._worker = worker
    closed: list[bool] = []
    real_destroy = root.destroy
    root.destroy = lambda: closed.append(True)
    try:
        app._on_close()
        assert app._closing is True
        assert app._cancel.is_set()
        assert not closed
        worker.alive = False
        app._wait_worker_then_close()
        assert closed
    finally:
        root.destroy = real_destroy
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_gui_unverified_prompt_returns_choice_without_env(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    monkeypatch.delenv(karen_install.UNVERIFIED_ENV, raising=False)
    monkeypatch.delenv("KAREN_REQUIRE_VERIFIED", raising=False)
    monkeypatch.delenv("KAREN_BACKEND_SHA256", raising=False)
    monkeypatch.delenv("KAREN_WEBUI_SHA256", raising=False)
    try:
        monkeypatch.setattr(
            gui_install.messagebox,
            "askyesnocancel",
            lambda *args, **kwargs: True,
        )
        assert (
            app._confirm_unverified_source(online=True, frontend="webui")
            is True
        )
        assert karen_install.UNVERIFIED_ENV not in os.environ

        monkeypatch.setattr(
            gui_install.messagebox,
            "askyesnocancel",
            lambda *args, **kwargs: False,
        )
        assert (
            app._confirm_unverified_source(online=True, frontend="webui")
            is False
        )
        assert karen_install.UNVERIFIED_ENV not in os.environ

        monkeypatch.setattr(
            gui_install.messagebox,
            "askyesnocancel",
            lambda *args, **kwargs: None,
        )
        assert (
            app._confirm_unverified_source(online=True, frontend="webui")
            is None
        )
    finally:
        os.environ.pop(karen_install.UNVERIFIED_ENV, None)
        root.destroy()


def test_gui_unverified_prompt_skipped_for_direct_mirror(monkeypatch, tmp_path):
    root, app, _messages = _make_app(tmp_path, monkeypatch)
    monkeypatch.delenv(karen_install.UNVERIFIED_ENV, raising=False)
    monkeypatch.delenv("KAREN_REQUIRE_VERIFIED", raising=False)
    called: list[bool] = []

    def _prompt(*_args, **_kwargs):
        called.append(True)
        return True

    monkeypatch.setattr(gui_install.messagebox, "askyesnocancel", _prompt)
    try:
        app.github_mirror.set("direct")
        assert (
            app._confirm_unverified_source(online=True, frontend="webui")
            is False
        )
        assert called == []
        assert karen_install.UNVERIFIED_ENV not in os.environ
    finally:
        root.destroy()
