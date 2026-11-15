#!/usr/bin/env python3
"""在 Xvfb 中驱动 Tk 安装器，录制视频并逐步骤截图。

CI 用法（必须已有 DISPLAY，并安装了 ImageMagick/ffmpeg）：

    python scripts/capture_installer_ui.py \
        --source demo-src --install-dir /tmp/kb-installer-demo --out artifacts

脚本直接实例化 ``gui_install.InstallerApp``，因此截到的是真实界面；
安装阶段调用真实的 ``core.install``（本地 WebUI 源码），不是假进度。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

import gui_install  # noqa: E402


class DemoDriver:
    def __init__(
        self,
        *,
        source: Path,
        install_dir: Path,
        out: Path,
        screen: str,
        record: bool,
        no_install: bool = False,
        no_mirror: bool = False,
    ) -> None:
        self.source = source.resolve()
        self.install_dir = install_dir.resolve()
        self.out = out.resolve()
        self.screen = screen
        self.record = record
        self.no_install = no_install
        self.no_mirror = no_mirror
        self.shots_dir = self.out / "screenshots"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.out / "driver.log"
        self.video_path = self.out / "installer-demo.mp4"
        self.root: tk.Tk | None = None
        self.app: gui_install.InstallerApp | None = None
        self.ffmpeg: subprocess.Popen[bytes] | None = None
        self._shot_count = 0
        self._stage_count = 0
        self._last_stage = ""
        self._last_periodic = 0.0
        self._periodic_count = 0
        self._finished = False
        self._exit_code = 1
        self._started = time.monotonic()
        self._original_dialogs: dict[str, object] = {}

    # ------------------------------------------------------------------
    # Logging / capture
    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        line = f"[capture] {message}"
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def shot(self, label: str) -> None:
        if self.root is None:
            return
        self.root.update_idletasks()
        self.root.update()
        self._shot_count += 1
        path = self.shots_dir / f"{self._shot_count:02d}-{label}.png"
        x = self.root.winfo_rootx()
        y = self.root.winfo_rooty()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        result = subprocess.run(
            [
                "import",
                "-window",
                "root",
                "-crop",
                f"{width}x{height}+{x}+{y}",
                "+repage",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.log(
                f"截图失败 {path.name}: "
                f"{result.stderr.strip()[:300]}"
            )
        else:
            self.log(f"截图 {path.name}（{width}x{height}+{x}+{y}）")

    def start_video(self) -> None:
        if not self.record:
            return
        display = os.environ.get("DISPLAY", ":99")
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "x11grab",
            "-framerate",
            "10",
            "-video_size",
            self.screen,
            "-i",
            display,
            "-t",
            "1800",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(self.video_path),
        ]
        try:
            self.ffmpeg = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self.log(f"无法启动 ffmpeg，将继续截图：{exc}")
            self.ffmpeg = None
            return
        time.sleep(1.0)
        if self.ffmpeg.poll() is not None:
            self.log("ffmpeg 启动失败，将继续截图")
            self.ffmpeg = None
        else:
            self.log(f"录屏开始：{display} {self.screen}")

    def stop_video(self) -> None:
        process = self.ffmpeg
        self.ffmpeg = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(b"q")
                process.stdin.flush()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.log(f"录屏结束：{self.video_path}")

    # ------------------------------------------------------------------
    # Tk dialogs
    # ------------------------------------------------------------------
    def _patch_dialogs(self) -> None:
        """自动化时不要让 messagebox 阻塞 Tk 主循环。"""
        box = gui_install.messagebox
        for name in ("showinfo", "showwarning", "showerror"):
            original = getattr(box, name)
            self._original_dialogs[name] = original

            def _fake(
                title: str = "",
                message: str = "",
                *args: object,
                _name: str = name,
                **kwargs: object,
            ) -> str:
                self.log(f"对话框 {_name}: {title} / {str(message)[:300]}")
                return "ok"

            setattr(box, name, _fake)
        original_ask = box.askyesno
        self._original_dialogs["askyesno"] = original_ask

        def _fake_ask(title: str = "", message: str = "", *args: object, **kwargs: object) -> bool:
            self.log(f"对话框 askyesno（自动确认）: {title} / {str(message)[:300]}")
            return True

        box.askyesno = _fake_ask  # type: ignore[assignment]

    def _restore_dialogs(self) -> None:
        box = gui_install.messagebox
        for name, original in self._original_dialogs.items():
            setattr(box, name, original)

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------
    def _prepare(self) -> None:
        if self.install_dir.exists():
            shutil.rmtree(self.install_dir, ignore_errors=True)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"源码目录：{self.source}")
        self.log(f"安装目录：{self.install_dir}")
        self.root = tk.Tk()
        self.app = gui_install.InstallerApp(self.root)
        self._patch_dialogs()
        self.root.after(1200, self.step_config)

    def step_config(self) -> None:
        assert self.app is not None
        self.shot("main-config")
        self.app.source_mode.set("source")
        self.app.source_path.set(str(self.source))
        self.app.frontend.set("webui")
        self.app.download_fonts.set(False)
        self.app.create_shortcut.set(False)
        self.app.install_dir.set(str(self.install_dir))
        self.app._update_source_state()
        self.app._update_appearance_state()
        self.root.after(600, self._after_config_updated)

    def _after_config_updated(self) -> None:
        assert self.app is not None
        self.shot("source-webui-selected")
        self.app.config_canvas.yview_moveto(1.0)
        self.root.after(500, self._after_scroll)

    def _after_scroll(self) -> None:
        assert self.app is not None
        self.shot("appearance-and-renderer")
        self.app.notebook.select(1)
        self.root.after(500, self._after_log_tab)

    def _after_log_tab(self) -> None:
        assert self.app is not None
        self.shot("install-log")
        self.app.notebook.select(0)
        self.app.config_canvas.yview_moveto(0.0)
        self.root.after(400, self._open_mirrors)

    def _open_mirrors(self) -> None:
        assert self.app is not None
        if self.no_mirror:
            self.log("跳过镜像测速（--no-mirror）")
            self.root.after(
                300,
                self._demo_only_finish if self.no_install else self._start_install,
            )
            return
        self.app._open_mirror_dialog()
        self.root.after(600, self._start_mirrors)

    def _start_mirrors(self) -> None:
        assert self.app is not None
        self.shot("mirror-dialog")
        self.app._start_mirror_test()
        self.root.after(1000, self._poll_mirrors)

    def _poll_mirrors(self) -> None:
        assert self.app is not None
        worker = self.app._mirror_worker
        if worker is not None and worker.is_alive():
            self.root.after(1000, self._poll_mirrors)
            return
        self.root.after(500, self._after_mirrors)

    def _after_mirrors(self) -> None:
        assert self.app is not None
        self.shot("mirror-results")
        self.app._close_mirror_dialog()
        if self.no_install:
            self.root.after(400, self._demo_only_finish)
            return
        self.root.after(400, self._start_install)

    def _demo_only_finish(self) -> None:
        self.shot("demo-only")
        self.finish(True, "no-install")

    def _start_install(self) -> None:
        assert self.app is not None
        self.app.notebook.select(0)
        self.app.config_canvas.yview_moveto(0.0)
        self.shot("before-install")
        self.log("开始真实安装（本地 WebUI 源码）")
        self.app._start_install()
        self.root.after(700, self._poll_install)

    def _poll_install(self) -> None:
        assert self.app is not None
        worker = self.app._worker
        stage = self.app.progress_step.get().strip()
        if stage and stage != self._last_stage:
            self._last_stage = stage
            self._stage_count += 1
            self.log(f"阶段：{stage}")
            self.shot(f"progress-{self._stage_count:02d}")
            self._last_periodic = time.monotonic()
        elif worker is not None:
            now = time.monotonic()
            if (
                now - self._last_periodic >= 8
                and self._periodic_count < 8
            ):
                self._periodic_count += 1
                self._last_periodic = now
                self.shot(f"progress-{self._stage_count:02d}-t{self._periodic_count}")
        if worker is None:
            status = self.app.status.get()
            self.shot("finished")
            self.log(f"最终状态：{status}")
            self.finish("安装完成" in status, status)
            return
        self.root.after(700, self._poll_install)

    def _timeout(self) -> None:
        if self._finished:
            return
        self.log("演示超时（20 分钟）")
        self.shot("timeout")
        self.finish(False, "timeout")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def finish(self, success: bool, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        self._exit_code = 0 if success else 1
        elapsed = time.monotonic() - self._started
        self.log(
            f"结束：success={success} reason={reason} "
            f"elapsed={elapsed:.1f}s shots={self._shot_count}"
        )
        self.stop_video()
        self._restore_dialogs()
        if self.root is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def run(self) -> int:
        self.log("演示驱动启动")
        try:
            self._prepare()
            self.start_video()
            self.root.after(20 * 60 * 1000, self._timeout)
            self.root.mainloop()
        except BaseException:
            traceback.print_exc()
            self.log("驱动异常：" + traceback.format_exc())
            self.finish(False, "exception")
        finally:
            self.stop_video()
            self._restore_dialogs()
        return self._exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--screen", default="1280x800")
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="只截图向导，不执行真实安装（本地冒烟用）",
    )
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="跳过镜像测速（本地冒烟用）",
    )
    args = parser.parse_args(argv)

    if not (args.source / "backend").is_dir():
        print(f"source backend missing: {args.source}", file=sys.stderr)
        return 2
    if not (args.source / "webui").is_dir():
        print(f"source webui missing: {args.source}", file=sys.stderr)
        return 2
    driver = DemoDriver(
        source=args.source,
        install_dir=args.install_dir,
        out=args.out,
        screen=args.screen,
        record=not args.no_video,
        no_install=args.no_install,
        no_mirror=args.no_mirror,
    )
    return driver.run()


if __name__ == "__main__":
    raise SystemExit(main())
