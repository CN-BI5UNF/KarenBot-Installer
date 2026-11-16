#!/usr/bin/env python3
"""KarenBot 安装引导 GUI（Tkinter）。

安装器只需要一个简单的原生向导：不嵌入浏览器、不依赖 GTK4，也不把
额外的浏览器运行时塞进安装包。Tkinter 随 CPython 提供，PyInstaller
可以把它打成 Windows/Linux 单文件。

界面分成“安装配置 / 安装日志”两页；外观页提供 GTK4 内置主题包和
渲染回退选项，实际启动参数由安装核心写入 launch-gui / launch-gui-safe。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

try:
    from . import karen_install as core
except ImportError:  # 直接运行 installer/gui_install.py 或 PyInstaller 冻结时
    import karen_install as core

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter import font as tkfont
    from tkinter.scrolledtext import ScrolledText
except ImportError as exc:  # pragma: no cover - 缺少 python3-tk 的系统
    tk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    tkfont = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    ScrolledText = None  # type: ignore[assignment]
    _TK_ERROR: Exception | None = exc
else:
    _TK_ERROR = None

APP_TITLE = "KarenBot 安装引导"
DEFAULT_PROBE_URL = core.DEFAULT_PROBE_URL


def _enable_dpi_awareness() -> None:
    """Windows 高分屏下避免 Tk 被系统拉伸成模糊位图。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001 - Windows 8.1 以下
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _configure_fonts(root: tk.Tk) -> str:
    if tkfont is None:
        return "TkDefaultFont"
    try:
        families = set(tkfont.families(root))
    except tk.TclError:
        return "TkDefaultFont"
    candidates = (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "DejaVu Sans",
    )
    family = next((name for name in candidates if name in families), "TkDefaultFont")
    if family != "TkDefaultFont":
        for name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
        ):
            try:
                tkfont.nametofont(name).configure(family=family, size=10)
            except tk.TclError:
                pass
    return family


def _print_console(text: str) -> None:
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(text, file=stream, flush=True)
    except Exception:  # noqa: BLE001 - Windows windowed 版没有控制台
        pass


def _open_path(path: Path) -> None:
    if os.name == "nt":
        startfile = getattr(os, "startfile", None)
        if startfile is not None:
            startfile(str(path))
            return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])


def _run_target(path: Path, cwd: Path) -> None:
    if os.name == "nt":
        _open_path(path)
        return
    subprocess.Popen([str(path)], cwd=str(cwd))


class InstallerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = 900
        # 高级选项默认收起，默认高度可以降到 560~640，避免 1366×768 /
        # 150% DPI 笔记本上窗口占满整个垂直屏幕。
        height = min(640, max(560, int(screen_height * 0.72)))
        x = max(0, (screen_width - width) // 2)
        y = max(0, min(20, screen_height - height - 60))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(800, 540)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.font_family = _configure_fonts(root)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._mirror_worker: threading.Thread | None = None
        self._mirror_window: tk.Toplevel | None = None
        self._mirror_tree: ttk.Treeview | None = None
        self._mirror_button: ttk.Button | None = None
        self._progress_window: tk.Toplevel | None = None
        self._dialog_progress: ttk.Progressbar | None = None
        self._progress_started: float | None = None
        self._progress_done = 0
        self._progress_total: int | None = None
        self._progress_samples: list[tuple[float, int]] = []
        self._cancel = threading.Event()
        self._installed_dir: Path | None = None
        self._installed_frontend = "gui"
        self._running = False
        self._uninstall_check: str | None = None
        self._skin_radios: list[ttk.Radiobutton] = []
        self._render_radios: list[ttk.Radiobutton] = []
        self._config_widgets: list[tuple[tk.Widget, str]] = []
        self._mirror_rows: dict[str, str] = {}
        self._mirror_apply_button: ttk.Button | None = None
        self._closing = False
        self._close_deadline = 0.0
        self._log_file = Path(tempfile.gettempdir()) / "karenbot-install.log"
        self._log_handle = None
        try:
            self._log_handle = self._log_file.open("w", encoding="utf-8")
        except OSError:
            self._log_handle = None
        self._build_vars()
        self._build_ui()
        self._update_source_state()
        self._update_appearance_state()
        self.install_dir.trace_add(
            "write", lambda *_args: self._schedule_uninstall_check()
        )
        self._update_uninstall_state()
        self._poll_queue()

    def _track(self, widget: tk.Widget, normal_state: str = "normal") -> None:
        """记录安装期间需要一起锁定的配置控件及其正常状态。"""
        self._config_widgets.append((widget, normal_state))

    def _build_vars(self) -> None:
        self.source_mode = tk.StringVar(value="online")
        self.source_path = tk.StringVar()
        self.archive_path = tk.StringVar()
        self.frontend = tk.StringVar(value="gui")
        self.install_dir = tk.StringVar(value=str(core.DEFAULT_INSTALL_DIR))
        self.create_shortcut = tk.BooleanVar(value=True)
        self.download_fonts = tk.BooleanVar(value=True)
        self.gui_skin = tk.StringVar(value="karen")
        self.render_mode = tk.StringVar(value="auto")
        self.pypi_mirror = tk.StringVar(value="auto")
        self.github_mirror = tk.StringVar(value="auto")
        self.mirror_url = tk.StringVar(value=core.default_probe_url("gui"))
        self.status = tk.StringVar(value="就绪")
        self.progress_step = tk.StringVar(value="准备安装…")
        # 进度弹窗单独一行显示下载速度/ETA，避免和阶段标题重复。
        self.dialog_status = tk.StringVar(value="")
        self.skin_note = tk.StringVar(value="")
        self.log_hint = tk.StringVar(value=f"自动保存：{self._log_file}")

    def _build_ui(self) -> None:
        # 不锁死高度：Windows 高 DPI / 不同字体度量下固定 56px 会把
        # 16pt 标题裁掉。让标签自己撑开，再补一点底部留白。
        header = tk.Frame(self.root, bg="#111827")
        header.pack(fill="x")
        tk.Label(
            header,
            text=APP_TITLE,
            bg="#111827",
            fg="#f9fafb",
            font=(self.font_family, 16, "bold"),
        ).pack(anchor="w", padx=18, pady=(10, 0))
        tk.Label(
            header,
            text="选择来源、界面和外观；安装器本身不内置应用包。",
            bg="#111827",
            fg="#cbd5e1",
            font=(self.font_family, 9),
        ).pack(anchor="w", padx=18, pady=(2, 10))

        body = ttk.Frame(self.root, padding=8)
        body.pack(fill="both", expand=True)

        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=(6, 0))
        self.mirror_open_button = ttk.Button(
            buttons, text="镜像测速…", command=self._open_mirror_dialog
        )
        self.mirror_open_button.pack(side="left")
        self.open_button = ttk.Button(
            buttons,
            text="打开安装目录",
            command=self._open_install_dir,
            state="disabled",
        )
        self.open_button.pack(side="left", padx=(6, 0))
        self.uninstall_button = ttk.Button(
            buttons,
            text="卸载…",
            command=self._start_uninstall,
            state="disabled",
        )
        self.uninstall_button.pack(side="left", padx=(6, 0))
        self.launch_button = ttk.Button(
            buttons, text="启动", command=self._launch_installed, state="disabled"
        )
        self.launch_button.pack(side="left", padx=(6, 0))
        self.launch_safe_button = ttk.Button(
            buttons,
            text="兼容模式启动",
            command=self._launch_installed_safe,
            state="disabled",
        )
        self.launch_safe_button.pack(side="left", padx=(6, 0))
        self.start_button = ttk.Button(
            buttons, text="开始安装", command=self._start_install
        )
        self.start_button.pack(side="right")
        self.cancel_button = ttk.Button(
            buttons,
            text="取消",
            command=self._cancel_install,
            state="disabled",
        )
        self.cancel_button.pack(side="right", padx=(0, 6))

        status_frame = ttk.Frame(body)
        status_frame.pack(side="bottom", fill="x", pady=(2, 4))
        self.progress = ttk.Progressbar(
            status_frame, mode="determinate", maximum=100
        )
        self.progress.pack(fill="x")
        ttk.Label(status_frame, textvariable=self.status).pack(anchor="w")

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="both", expand=True)
        config_tab = ttk.Frame(self.notebook)
        log_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(config_tab, text="安装配置")
        self.notebook.add(log_tab, text="安装日志")

        self._build_config_scroll(config_tab)
        self._build_source_section(self.config_inner)
        self._build_frontend_section(self.config_inner)
        self._build_install_section(self.config_inner)
        self._build_advanced_section(self.config_inner)

        log_tools = ttk.Frame(log_tab)
        log_tools.pack(fill="x", pady=(0, 4))
        ttk.Button(
            log_tools, text="复制日志", command=self._copy_log
        ).pack(side="left")
        ttk.Button(
            log_tools, text="保存日志…", command=self._save_log
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            log_tools,
            textvariable=self.log_hint,
            foreground="#64748b",
        ).pack(side="left", padx=(8, 0))
        self.log_text = ScrolledText(
            log_tab, height=12, wrap="word", state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)

        self._log_line(f"安装器版本：{core.INSTALLER_VERSION}")
        self._log_line(f"安装目录默认：{core.DEFAULT_INSTALL_DIR}")
        self._log_line("在线安装：优先稳定版；没有稳定版时使用最新版。")
        self._log_line("镜像：安装时会自动测速，无需先手动点击“镜像测速”。")
        self._log_line("Python 环境不完整时，会自动下载便携 Python 到安装目录。")

    def _build_config_scroll(self, parent: ttk.Frame) -> None:
        """小屏/高 DPI 下配置页可滚动，避免外观/渲染选项被裁掉。"""
        style = ttk.Style(self.root)
        background = style.lookup("TFrame", "background") or "#f0f0f0"
        canvas = tk.Canvas(
            parent,
            highlightthickness=0,
            borderwidth=0,
            background=background,
        )
        scrollbar = ttk.Scrollbar(
            parent, orient="vertical", command=canvas.yview
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        inner = ttk.Frame(canvas, padding=8)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(event) -> None:
            canvas.itemconfigure(window, width=event.width)

        def _on_wheel(event) -> None:
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            else:
                delta = int(getattr(event, "delta", 0) / 120)
                canvas.yview_scroll(-3 * delta if delta else 0, "units")

        inner.bind("<Configure>", _sync_region)
        canvas.bind("<Configure>", _sync_width)
        for widget in (canvas, inner):
            widget.bind("<MouseWheel>", _on_wheel)
            widget.bind("<Button-4>", _on_wheel)
            widget.bind("<Button-5>", _on_wheel)
        self.config_canvas = canvas
        self.config_inner = inner

    def _wrap_label(self, widget: tk.Widget, margin: int = 52) -> None:
        """让长说明随配置区宽度换行，避免右侧被 Canvas 裁掉。"""
        def _sync(event: tk.Event | None = None) -> None:
            width = event.width if event is not None else self.config_canvas.winfo_width()
            if width > 100:
                widget.configure(wraplength=max(260, int(width) - margin))

        self.config_canvas.bind("<Configure>", _sync, add="+")
        _sync()

    def _build_source_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="1. 安装来源", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        frame.columnconfigure(1, weight=1)

        self.source_radios = []
        for row, text, value in (
            (0, "在线安装（自动选择稳定版/最新版）", "online"),
            (1, "本地源码目录", "source"),
            (2, "本地安装包", "archive"),
        ):
            radio = ttk.Radiobutton(
                frame,
                text=text,
                variable=self.source_mode,
                value=value,
                command=self._update_source_state,
            )
            radio.grid(
                row=row,
                column=0,
                columnspan=3 if row == 0 else 1,
                sticky="w",
                pady=2,
            )
            self.source_radios.append(radio)
            self._track(radio)
        self.source_entry = ttk.Entry(frame, textvariable=self.source_path)
        self.source_entry.grid(row=1, column=1, sticky="ew", padx=(8, 6), pady=2)
        self.source_button = ttk.Button(
            frame, text="浏览…", command=self._browse_source
        )
        self.source_button.grid(row=1, column=2, pady=2)

        self.archive_entry = ttk.Entry(frame, textvariable=self.archive_path)
        self.archive_entry.grid(row=2, column=1, sticky="ew", padx=(8, 6), pady=2)
        self.archive_button = ttk.Button(
            frame, text="浏览…", command=self._browse_archive
        )
        self.archive_button.grid(row=2, column=2, pady=2)
        for widget in (
            self.source_entry,
            self.source_button,
            self.archive_entry,
            self.archive_button,
        ):
            self._track(widget)

        source_hint = ttk.Label(
            frame,
            text=(
                "在线安装会下载 backend 核心包和 GUI/WebUI 模块；"
                "Windows 原生 GUI 需要安装器 Release 的 GTK4 便携预构建包。"
            ),
            foreground="#64748b",
        )
        source_hint.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self._wrap_label(source_hint)

    def _build_frontend_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="2. 界面版本", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.frontend_radios = []
        for text, value, padx in (
            ("原生 GUI（GTK4，推荐桌面）", "gui", (0, 0)),
            ("WebUI（浏览器控制台）", "webui", (18, 0)),
        ):
            radio = ttk.Radiobutton(
                row,
                text=text,
                variable=self.frontend,
                value=value,
                command=self._update_appearance_state,
            )
            radio.pack(side="left", padx=padx)
            self.frontend_radios.append(radio)
            self._track(radio)
        frontend_hint = ttk.Label(
            frame,
            text=(
                "GUI 需要 GTK4（Windows 用便携预构建包）；"
                "WebUI 走浏览器，无需 GTK，文件以浏览器下载/内嵌打开为主。"
            ),
            foreground="#64748b",
            justify="left",
        )
        frontend_hint.pack(anchor="w", pady=(2, 0))
        self._wrap_label(frontend_hint)

    def _build_install_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="3. 安装位置", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="目录").grid(row=0, column=0, sticky="w")
        self.install_entry = ttk.Entry(frame, textvariable=self.install_dir)
        self.install_entry.grid(row=0, column=1, sticky="ew", padx=(8, 6))
        self.install_browse_button = ttk.Button(
            frame, text="浏览…", command=self._browse_install_dir
        )
        self.install_browse_button.grid(row=0, column=2)
        self._track(self.install_entry)
        self._track(self.install_browse_button)
        install_hint = ttk.Label(
            frame,
            text=(
                "默认目录在用户 AppData，通常不需要管理员权限；"
                "Windows 路径请不要包含中文或其他非 ASCII 字符。"
            ),
            foreground="#64748b",
        )
        install_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))
        self._wrap_label(install_hint)

    def _build_advanced_section(self, body: ttk.Frame) -> None:
        """镜像/字体/外观默认收起，小屏打开即可看到“开始安装”。"""
        frame = ttk.Frame(body)
        frame.pack(fill="x", pady=(0, 3))
        self.advanced_button = ttk.Button(
            frame,
            text="高级设置（镜像 / 字体 / 外观）",
            command=self._toggle_advanced,
        )
        self.advanced_button.pack(fill="x")
        self._track(self.advanced_button)
        self.advanced_body = ttk.Frame(frame)
        self._advanced_open = False
        self._build_options_section(self.advanced_body)
        self._build_appearance_section(self.advanced_body)

    def _toggle_advanced(self) -> None:
        if self._running:
            return
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.advanced_body.pack(fill="x")
            self.advanced_button.configure(text="收起高级设置")
        else:
            self.advanced_body.pack_forget()
            self.advanced_button.configure(
                text="高级设置（镜像 / 字体 / 外观）"
            )
        try:
            self.config_inner.update_idletasks()
            self.config_canvas.configure(
                scrollregion=self.config_canvas.bbox("all")
            )
        except tk.TclError:
            pass

    def _build_options_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="4. 选项", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.create_shortcut_box = ttk.Checkbutton(
            row,
            text="创建快捷方式",
            variable=self.create_shortcut,
        )
        self.create_shortcut_box.pack(side="left")
        self.download_fonts_box = ttk.Checkbutton(
            row,
            text="下载中文字体（Noto Sans/Serif SC，自动拉取）",
            variable=self.download_fonts,
        )
        self.download_fonts_box.pack(side="left", padx=(14, 0))
        self._track(self.create_shortcut_box)
        self._track(self.download_fonts_box)
        pypi_row = ttk.Frame(frame)
        pypi_row.pack(fill="x", pady=(4, 0))
        ttk.Label(pypi_row, text="PyPI 镜像").pack(side="left")
        pypi_box = ttk.Combobox(
            pypi_row,
            textvariable=self.pypi_mirror,
            state="readonly",
            width=12,
            values=("auto", *core.PYPI_MIRRORS),
        )
        pypi_box.pack(side="left", padx=(8, 0))
        self._track(pypi_box, "readonly")
        ttk.Label(
            pypi_row,
            text="auto 会按速度选择；国内网络通常清华/阿里更快。",
            foreground="#64748b",
        ).pack(side="left", padx=(8, 0))
        mirror_row = ttk.Frame(frame)
        mirror_row.pack(fill="x", pady=(4, 0))
        ttk.Label(mirror_row, text="GitHub 镜像").pack(side="left")
        self.github_mirror_box = ttk.Combobox(
            mirror_row,
            textvariable=self.github_mirror,
            state="readonly",
            width=18,
            values=("auto", *[str(item.get("name")) for item in core.load_mirrors()]),
        )
        self.github_mirror_box.pack(side="left", padx=(8, 0))
        self._track(self.github_mirror_box, "readonly")
        ttk.Label(
            mirror_row,
            text="auto 自动测速；也可在“镜像测速…”弹窗里双击一行固定。",
            foreground="#64748b",
        ).pack(side="left", padx=(8, 0))

    def _build_appearance_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="5. 外观与渲染（原生 GUI）", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        self.appearance_frame = frame

        skin_row = ttk.Frame(frame)
        skin_row.pack(fill="x")
        ttk.Label(skin_row, text="主题包").pack(side="left")
        for value, label in (
            ("karen", "Karen 暖色（目标机同款）"),
            ("system", "系统 Adwaita"),
            ("none", "不使用美化包"),
        ):
            radio = ttk.Radiobutton(
                skin_row,
                text=label,
                variable=self.gui_skin,
                value=value,
                command=self._update_skin_note,
            )
            radio.pack(side="left", padx=(10, 0))
            self._skin_radios.append(radio)
            self._track(radio)

        render_row = ttk.Frame(frame)
        render_row.pack(fill="x", pady=(4, 0))
        ttk.Label(render_row, text="渲染").pack(side="left")
        for value, label in (
            ("auto", "自动回退（推荐）"),
            ("cairo", "强制软件渲染"),
            ("gl", "强制硬件渲染"),
        ):
            radio = ttk.Radiobutton(
                render_row,
                text=label,
                variable=self.render_mode,
                value=value,
            )
            radio.pack(side="left", padx=(10, 0))
            self._render_radios.append(radio)
            self._track(radio)

        self.skin_note_label = ttk.Label(
            frame, textvariable=self.skin_note, foreground="#b45309"
        )
        self.skin_note_label.pack(anchor="w", pady=(3, 2))

        appearance_hint = ttk.Label(
            frame,
            text=(
                "自动模式由 GUI 启动器在初始化 GTK 前按 stage 回退："
                "GSK_RENDERER=cairo / GDK_DEBUG=gl-disable / LIBGL_ALWAYS_SOFTWARE=1。\n"
                "仍黑屏时可用“兼容模式启动”或桌面上的“KarenBot GUI (兼容模式)”。"
            ),
            foreground="#64748b",
            justify="left",
        )
        appearance_hint.pack(anchor="w", pady=(2, 0))
        self._wrap_label(appearance_hint)

    def _update_source_state(self) -> None:
        mode = self.source_mode.get()
        source_enabled = mode == "source" and not self._running
        archive_enabled = mode == "archive" and not self._running
        self.source_entry.configure(
            state="normal" if source_enabled else "disabled"
        )
        self.source_button.configure(
            state="normal" if source_enabled else "disabled"
        )
        self.archive_entry.configure(
            state="normal" if archive_enabled else "disabled"
        )
        self.archive_button.configure(
            state="normal" if archive_enabled else "disabled"
        )

    def _update_appearance_state(self) -> None:
        enabled = self.frontend.get() == "gui" and not self._running
        state = "normal" if enabled else "disabled"
        for radio in self._skin_radios:
            radio.configure(state=state)
        for radio in self._render_radios:
            radio.configure(state=state)
        self._sync_probe_url()
        self._update_skin_note()

    def _sync_probe_url(self) -> None:
        """界面切换时把默认测速 URL 对准实际要下载的资产类型。"""
        current = self.mirror_url.get().strip()
        known = {
            "",
            core.DEFAULT_PROBE_URL,
            core.default_probe_url("gui"),
            core.default_probe_url("webui"),
        }
        if current in known:
            self.mirror_url.set(core.default_probe_url(self.frontend.get()))

    def _update_skin_note(self) -> None:
        if self.frontend.get() != "gui":
            self.skin_note.set("WebUI 不使用 GTK 主题包。")
            self.skin_note_label.configure(foreground="#64748b")
            return
        if self.gui_skin.get() == "none":
            self.skin_note.set(
                "注意：不使用美化包时，Windows 下 GTK4 默认外观可能较差，"
                "安装器不提供额外方案，需要自行解决。"
            )
            self.skin_note_label.configure(foreground="#b91c1c")
            return
        self.skin_note.set(
            "Karen 暖色最接近 Linux 目标机；Adwaita 是 GTK4 默认皮肤。"
            "两者都不需要额外下载主题资产。"
        )
        self.skin_note_label.configure(foreground="#64748b")

    def _browse_source(self) -> None:
        path = filedialog.askdirectory(title="选择 KarenBot 源码目录")
        if path:
            self.source_path.set(path)
            self.source_mode.set("source")
            self._update_source_state()

    def _browse_archive(self) -> None:
        path = filedialog.askopenfilename(
            title="选择安装包",
            filetypes=[
                ("KarenBot 安装包", "*.tar.gz *.tgz *.tar *.zip"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.archive_path.set(path)
            self.source_mode.set("archive")
            self._update_source_state()

    def _browse_install_dir(self) -> None:
        path = filedialog.askdirectory(title="选择安装目录")
        if path:
            self.install_dir.set(path)

    def _log_line(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if self._log_handle is not None:
            try:
                self._log_handle.write(line + "\n")
                self._log_handle.flush()
            except OSError:
                try:
                    self._log_handle.close()
                except OSError:
                    pass
                self._log_handle = None

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        if self._log_handle is not None:
            try:
                self._log_handle.seek(0)
                self._log_handle.truncate()
                self._log_handle.flush()
            except OSError:
                pass

    def _log_text_value(self) -> str:
        return self.log_text.get("1.0", "end-1c")

    def _copy_log(self) -> None:
        text = self._log_text_value()
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError as exc:
            messagebox.showerror("复制失败", str(exc))
            return
        self.status.set("日志已复制到剪贴板")

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="保存安装日志",
            defaultextension=".log",
            initialfile="karenbot-install.log",
            filetypes=[("日志文件", "*.log"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(self._log_text_value(), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.status.set(f"日志已保存：{path}")

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")
        self.mirror_open_button.configure(
            state="disabled" if running else "normal"
        )
        if self._mirror_button is not None:
            self._mirror_button.configure(
                state="disabled" if running else "normal"
            )
        if self._mirror_apply_button is not None:
            self._mirror_apply_button.configure(
                state="disabled" if running else "normal"
            )
        for widget, normal_state in self._config_widgets:
            widget.configure(state="disabled" if running else normal_state)
        if running:
            for radio in self._skin_radios + self._render_radios:
                radio.configure(state="disabled")
            self.uninstall_button.configure(state="disabled")
            self.open_button.configure(state="disabled")
            self.launch_button.configure(state="disabled")
            self.launch_safe_button.configure(state="disabled")
        else:
            self._update_source_state()
            self._update_appearance_state()
            self._update_uninstall_state()

    def _schedule_uninstall_check(self) -> None:
        if self._uninstall_check is not None:
            try:
                self.root.after_cancel(self._uninstall_check)
            except tk.TclError:
                pass
        self._uninstall_check = self.root.after(400, self._update_uninstall_state)

    def _update_uninstall_state(self) -> None:
        self._uninstall_check = None
        if self._running:
            self.uninstall_button.configure(state="disabled")
            return
        raw = self.install_dir.get().strip()
        install_dir = Path(raw).expanduser() if raw else None
        enabled = False
        if install_dir is not None:
            try:
                enabled = core._looks_like_karenbot_install(install_dir)
            except (OSError, ValueError):
                enabled = False
        self.uninstall_button.configure(state="normal" if enabled else "disabled")
        if not enabled or install_dir is None:
            self._installed_dir = None
            self.open_button.configure(state="disabled")
            self.launch_button.configure(state="disabled")
            self.launch_safe_button.configure(state="disabled")
            return
        marker = core.read_install_marker(install_dir) or {}
        frontend = str(marker.get("frontend") or "").casefold()
        if frontend not in {"gui", "webui"}:
            try:
                modules = json.loads(
                    (install_dir / "app" / "modules.json").read_text(
                        encoding="utf-8"
                    )
                )
                frontend = str(modules.get("frontend") or "gui").casefold()
            except (OSError, ValueError):
                frontend = "gui"
        if frontend not in {"gui", "webui"}:
            frontend = "gui"
        self._installed_dir = install_dir
        self._installed_frontend = frontend
        self.open_button.configure(state="normal")
        self.launch_button.configure(state="normal")
        self.launch_safe_button.configure(state="normal")

    def _queue_log(self, line: str) -> None:
        self._queue.put(("log", line))

    def _queue_progress(self, done: int, total: int | None) -> None:
        self._queue.put(("progress", (done, total)))

    def _queue_status(self, message: str) -> None:
        self._queue.put(("stage", message))

    def _confirm_unverified_source(
        self, *, online: bool, frontend: str
    ) -> bool | None:
        """返回 True=允许第三方镜像，False=仅直连，None=取消安装。

        不再修改 ``os.environ``：用户这次的选择通过 ``allow_unverified``
        显式传给核心，避免同一进程内后续安装被上一次选择污染。
        """
        if not online:
            return False
        if os.name == "nt" and frontend == "gui":
            # Windows 便携包本身强制要求可信 SHA256，不需要这个提示。
            return False
        if self.github_mirror.get().strip().casefold() == "direct":
            # 已经明确固定 GitHub 直连，弹窗只会制造困惑。
            return False
        enabled = os.environ.get(core.UNVERIFIED_ENV, "").strip().casefold()
        if enabled in {"1", "true", "yes", "on"}:
            return True
        required = os.environ.get(
            "KAREN_REQUIRE_VERIFIED", ""
        ).strip().casefold()
        if required in {"1", "true", "yes", "on"}:
            # 核心会给出明确的缺少哈希错误。
            return False
        backend_hash = os.environ.get("KAREN_BACKEND_SHA256", "").strip()
        frontend_hash = os.environ.get(
            f"KAREN_{frontend.upper()}_SHA256", ""
        ).strip()
        if backend_hash and frontend_hash:
            return False
        answer = messagebox.askyesnocancel(
            "源码包没有发布哈希",
            "backend/frontend 使用 GitHub 自动源码归档，上游没有发布 SHA256。\n\n"
            "【是】允许第三方镜像加速（快，但镜像可能篡改源码）\n"
            "【否】仅使用 GitHub 直连（更安全，部分网络可能连不上）\n"
            "【取消】中止安装\n\n"
            "也可以通过 KAREN_BACKEND_SHA256 / "
            f"KAREN_{frontend.upper()}_SHA256 提供哈希。",
        )
        if answer is None:
            return None
        return bool(answer)

    def _start_install(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        source_mode = self.source_mode.get()
        online = source_mode == "online"
        source_dir: Path | None = None
        archive: Path | None = None
        if source_mode == "source":
            source_dir = Path(self.source_path.get().strip()).expanduser()
            if not source_dir.is_dir():
                messagebox.showerror("路径无效", "请选择有效的源码目录。")
                return
        elif source_mode == "archive":
            archive = Path(self.archive_path.get().strip()).expanduser()
            if not archive.is_file():
                messagebox.showerror("路径无效", "请选择有效的安装包。")
                return
        raw_dir = self.install_dir.get().strip()
        if not raw_dir:
            messagebox.showerror("路径无效", "安装目录不能为空。")
            return
        install_dir = Path(raw_dir).expanduser()
        for path, label in (
            (install_dir, "安装目录"),
            (source_dir, "源码目录"),
            (archive, "安装包路径"),
        ):
            if path is None:
                continue
            problem = core.path_problem(path, label)
            if problem:
                messagebox.showerror("路径包含中文", problem)
                return
        try:
            core.ensure_writable_dir(install_dir, "安装目录")
        except core.InstallFailure as exc:
            messagebox.showerror("安装目录不可写", exc.message)
            return
        try:
            core.set_mirror_override(self.github_mirror.get())
        except core.InstallFailure as exc:
            messagebox.showerror("镜像无效", exc.message)
            return
        if (install_dir / "app").exists() and not messagebox.askyesno(
            "覆盖安装",
            f"{install_dir / 'app'} 已存在。继续会替换应用目录，"
            "数据目录不会删除。是否继续？",
        ):
            return
        frontend = self.frontend.get()
        if os.name == "nt" and frontend == "gui":
            if source_dir is not None:
                messagebox.showerror(
                    "Windows GUI 安装",
                    "Windows 下原生 GUI 不能从源码安装。\n"
                    "请选择在线安装（下载 GTK4 便携包），或选择本地预构建 zip。",
                )
                return
            if self.gui_skin.get() == "none" and not messagebox.askyesno(
                "不使用美化包",
                "你没有选择 Windows GTK4 主题包。\n"
                "Windows 下默认外观可能不如 Linux，安装器不提供额外方案，"
                "需要你自行解决界面观感问题。\n\n仍然继续？",
            ):
                return

        allow_unverified = self._confirm_unverified_source(
            online=online, frontend=frontend
        )
        if allow_unverified is None:
            return
        self._installed_dir = None
        self._installed_frontend = frontend
        self._closing = False
        self._close_deadline = 0.0
        self._cancel.clear()
        self._progress_started = None
        self._progress_done = 0
        self._progress_total = None
        self._progress_samples.clear()
        self._set_running(True)
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.progress_step.set("准备安装…")
        self.status.set("准备安装…")
        self._open_progress_dialog()
        self._clear_log()
        self.notebook.select(1)
        source_desc = (
            "在线安装（自动选择稳定版/最新版）"
            if online
            else str(source_dir or archive)
        )
        gui_skin = self.gui_skin.get() if frontend == "gui" else "none"
        self._log_line(f"来源：{source_desc}")
        self._log_line(
            f"界面：{frontend}；安装目录：{install_dir}；"
            f"主题：{core.GUI_SKINS.get(gui_skin, gui_skin)}；"
            f"渲染：{self.render_mode.get()}；"
            f"GitHub 镜像：{self.github_mirror.get()}"
        )
        core.set_log_sink(self._queue_log)
        core.set_progress_sink(self._queue_progress)
        core.set_status_sink(self._queue_status)
        self._worker = threading.Thread(
            target=self._run_install,
            kwargs={
                "frontend": frontend,
                "install_dir": install_dir,
                "source_dir": source_dir,
                "archive": archive,
                "venv": None,
                "skip_deps": False,
                "create_shortcut": self.create_shortcut.get(),
                "download_fonts": self.download_fonts.get(),
                "cancel_event": self._cancel,
                "online": online,
                "release_tag": core.ONLINE_TAG,
                "gui_skin": gui_skin,
                "render_mode": self.render_mode.get(),
                "pypi_mirror": self.pypi_mirror.get(),
                "allow_unverified": bool(allow_unverified),
            },
            daemon=True,
        )
        self._worker.start()

    def _run_install(self, **kwargs: Any) -> None:
        try:
            result = core.install(**kwargs)
        except core.InstallCancelled:
            self._queue.put(("cancelled", None))
        except core.InstallFailure as exc:
            self._queue.put(("error", exc.message))
        except Exception:  # noqa: BLE001
            self._queue.put(("error", traceback.format_exc()))
        else:
            self._queue.put(("done", str(result)))
        finally:
            core.set_log_sink(None)
            core.set_progress_sink(None)
            core.set_status_sink(None)

    def _poll_queue(self) -> None:
        # pip 会刷出大量日志；每次最多处理 500 条，避免一次卡住 UI。
        processed = 0
        while processed < 500:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if kind == "log":
                self._log_line(str(payload))
            elif kind == "progress":
                done, total = payload
                self._update_progress(int(done), total)
            elif kind == "stage":
                self._on_stage(str(payload))
            elif kind == "done":
                self._on_done(Path(str(payload)))
            elif kind == "error":
                self._on_error(str(payload))
            elif kind == "cancelled":
                self._on_cancelled()
            elif kind == "mirror":
                self._show_mirrors(payload)
            elif kind == "mirror_error":
                self._on_mirror_error(str(payload))
            elif kind == "uninstalled":
                self._on_uninstalled(Path(str(payload)))
        self.root.after(1 if not self._queue.empty() else 80, self._poll_queue)

    def _on_stage(self, message: str) -> None:
        self.progress_step.set(message)
        self.status.set(message)
        self.dialog_status.set("")

    def _update_progress(self, done: int, total: int | None) -> None:
        dialog = self._dialog_progress
        now = time.monotonic()
        done = max(0, int(done))
        # 新文件（或重新开始）时清空滑动窗口，避免把上一段的平均速度带进来。
        if total != self._progress_total or done < self._progress_done:
            self._progress_total = total
            self._progress_started = now if done == 0 else None
            self._progress_samples.clear()
        self._progress_done = done
        samples = self._progress_samples
        samples.append((now, done))
        cutoff = now - 3.0
        while len(samples) > 2 and samples[0][0] < cutoff:
            samples.pop(0)
        # 最近 3 秒的瞬时速度；不足 0.6 秒不显示，避免首块算出几百 MB/s。
        speed = 0.0
        span = 0.0
        if len(samples) >= 2:
            span = now - samples[0][0]
            delta = done - samples[0][1]
            if span >= 0.6 and delta >= 0:
                speed = delta / span
        speed_text = (
            f" · {speed / 1024 / 1024:.1f} MB/s" if speed > 0 and span >= 0.6 else ""
        )
        if total and total > 0:
            if str(self.progress.cget("mode")) != "determinate":
                self.progress.stop()
            self.progress.configure(
                mode="determinate", maximum=total, value=done
            )
            if dialog is not None:
                if str(dialog.cget("mode")) != "determinate":
                    dialog.stop()
                dialog.configure(
                    mode="determinate", maximum=total, value=done
                )
            eta_text = ""
            if speed > 0:
                remaining = max(0.0, (total - done) / speed)
                if remaining >= 1:
                    eta_text = f" · 剩余约 {remaining:.0f} 秒"
            self.status.set(
                f"下载中 {done / 1024 / 1024:.1f} / "
                f"{total / 1024 / 1024:.1f} MB{speed_text}{eta_text}"
            )
            self.dialog_status.set(
                f"{done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB"
                f"{speed_text}{eta_text}"
            )
            return
        if str(self.progress.cget("mode")) != "indeterminate":
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        if dialog is not None and str(dialog.cget("mode")) != "indeterminate":
            dialog.configure(mode="indeterminate")
            dialog.start(12)
        self.status.set(
            f"已下载 {done / 1024 / 1024:.1f} MB{speed_text}"
        )
        self.dialog_status.set(
            f"已下载 {done / 1024 / 1024:.1f} MB{speed_text}"
        )

    def _on_done(self, path: Path) -> None:
        self._worker = None
        self._close_progress_dialog()
        self._set_running(False)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=100)
        self.status.set("安装完成")
        self._installed_dir = path
        self.open_button.configure(state="normal")
        self.launch_button.configure(state="normal")
        self.launch_safe_button.configure(state="normal")
        self.uninstall_button.configure(state="normal")
        self._log_line(f"安装完成：{path}")
        self._log_line("安装器已保留；需要修复或卸载时可再次运行它。")
        target = core.launch_target(path, self._installed_frontend)
        if not self._closing:
            messagebox.showinfo(
                "安装完成",
                f"KarenBot 已安装到：\n{path}\n\n启动入口：\n{target}\n\n"
                "如果启动后黑屏/崩溃，请使用“兼容模式启动”。",
            )

    def _on_error(self, message: str) -> None:
        self._worker = None
        self._close_progress_dialog()
        self._set_running(False)
        self.progress.stop()
        self.status.set("安装失败")
        self._log_line("错误：" + message)
        if not self._closing:
            messagebox.showerror("安装失败", message[:2000])

    def _on_cancelled(self) -> None:
        self._worker = None
        self._close_progress_dialog()
        self._set_running(False)
        self.progress.stop()
        self.status.set("已取消")
        self._log_line("安装已取消。")

    def _start_uninstall(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        raw = self.install_dir.get().strip()
        if not raw:
            messagebox.showerror("路径无效", "安装目录不能为空。")
            return
        install_dir = Path(raw).expanduser()
        try:
            installed = core._looks_like_karenbot_install(install_dir)
        except (OSError, ValueError):
            installed = False
        if not installed:
            messagebox.showerror(
                "未检测到安装",
                f"{install_dir}\n不是 KarenBot 安装目录（缺少安装标记）。",
            )
            return
        if not messagebox.askyesno(
            "卸载 KarenBot",
            "将删除以下内容：\n"
            f"{install_dir}\n\n"
            "包括程序、启动器、快捷方式、字体和主题偏好；"
            "运行数据目录（data/ 或旧版 app/data）也会一起删除。\n\n"
            "确定卸载？",
        ):
            return
        self._cancel.clear()
        self._closing = False
        self._close_deadline = 0.0
        self._progress_started = None
        self._progress_done = 0
        self._progress_total = None
        self._progress_samples.clear()
        self._set_running(True)
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.progress_step.set("准备卸载…")
        self.status.set("准备卸载…")
        self._open_progress_dialog(
            title="正在卸载 KarenBot",
            cancel_text="取消卸载",
            note="正在删除程序、启动器、快捷方式和运行数据；请不要手动打开或删除安装目录。",
        )
        self._clear_log()
        self.notebook.select(1)
        self._log_line(f"卸载：{install_dir}")
        core.set_log_sink(self._queue_log)
        core.set_progress_sink(self._queue_progress)
        core.set_status_sink(self._queue_status)
        self._worker = threading.Thread(
            target=self._run_uninstall,
            kwargs={"install_dir": install_dir},
            daemon=True,
        )
        self._worker.start()

    def _run_uninstall(self, *, install_dir: Path) -> None:
        try:
            result = core.uninstall(install_dir, cancel_event=self._cancel)
        except core.InstallCancelled:
            self._queue.put(("cancelled", None))
        except core.InstallFailure as exc:
            self._queue.put(("error", exc.message))
        except Exception:  # noqa: BLE001
            self._queue.put(("error", traceback.format_exc()))
        else:
            self._queue.put(("uninstalled", str(result)))
        finally:
            core.set_log_sink(None)
            core.set_progress_sink(None)
            core.set_status_sink(None)

    def _on_uninstalled(self, path: Path) -> None:
        self._worker = None
        self._close_progress_dialog()
        self._set_running(False)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.status.set("已卸载")
        self._installed_dir = None
        self.open_button.configure(state="disabled")
        self.launch_button.configure(state="disabled")
        self.launch_safe_button.configure(state="disabled")
        self.uninstall_button.configure(state="disabled")
        self._log_line(f"已卸载：{path}")
        if not self._closing:
            messagebox.showinfo("卸载完成", f"KarenBot 已卸载：\n{path}")

    def _cancel_install(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            return
        self._cancel.set()
        self.cancel_button.configure(state="disabled")
        self.progress_step.set("正在取消…")
        self.status.set("正在取消…")
        self._log_line("已请求取消；正在中断当前操作…")

    def _open_progress_dialog(
        self,
        *,
        title: str = "正在安装 KarenBot",
        cancel_text: str = "取消安装",
        note: str = (
            "安装器会自动测速 GitHub 镜像，不需要先手动点“镜像测速”。\n"
            "如果长时间无响应，请检查网络、代理、DNS 或防火墙。"
        ),
    ) -> None:
        if self._progress_window is not None:
            try:
                if self._progress_window.winfo_exists():
                    self._progress_window.lift()
                    return
            except tk.TclError:
                pass
            self._progress_window = None
        window = tk.Toplevel(self.root)
        window.title(title)
        window.transient(self.root)
        window.resizable(False, False)
        self.dialog_status.set("")
        self.root.update_idletasks()
        width = 580
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - width) // 2
        )
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - 260) // 2
        )
        # 先给一个临时尺寸，等控件按当前 DPI/字体排完后按 reqheight
        # 重新定高，避免“取消安装”按钮或说明文字被固定高度裁掉。
        window.geometry(f"{width}x1+{x}+{y}")
        window.protocol("WM_DELETE_WINDOW", self._cancel_install)
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=title,
            font=(self.font_family, 13, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            textvariable=self.progress_step,
            wraplength=540,
            justify="left",
        ).pack(anchor="w", pady=(8, 10))
        self._dialog_progress = ttk.Progressbar(
            body, mode="indeterminate", length=540
        )
        self._dialog_progress.pack(fill="x")
        self._dialog_progress.start(12)
        ttk.Label(
            body,
            textvariable=self.dialog_status,
            wraplength=540,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(
            body,
            text=note,
            foreground="#64748b",
            justify="left",
            wraplength=540,
        ).pack(anchor="w", pady=(10, 12))
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(
            actions, text="查看日志", command=self._show_log_tab
        ).pack(side="left")
        ttk.Button(
            actions, text=cancel_text, command=self._cancel_install
        ).pack(side="right")
        window.update_idletasks()
        height = max(230, window.winfo_reqheight())
        window.minsize(width, height)
        window.geometry(f"{width}x{height}+{x}+{y}")
        self._progress_window = window

    def _show_log_tab(self) -> None:
        # transient 进度窗会一直浮在主窗口上方；查看日志时直接关闭，
        # 主窗口底部仍有进度条和状态行。
        self._close_progress_dialog()
        try:
            self.root.deiconify()
            self.root.lift()
            self.notebook.select(1)
        except tk.TclError:
            pass

    def _close_progress_dialog(self) -> None:
        window = self._progress_window
        self._progress_window = None
        self._dialog_progress = None
        if window is None:
            return
        try:
            window.grab_release()
        except tk.TclError:
            pass
        try:
            window.destroy()
        except tk.TclError:
            pass

    def _open_mirror_dialog(self) -> None:
        if self._mirror_window is not None:
            try:
                if self._mirror_window.winfo_exists():
                    self._mirror_window.lift()
                    return
            except tk.TclError:
                pass
            self._mirror_window = None
        window = tk.Toplevel(self.root)
        window.title("GitHub 镜像测速")
        window.minsize(680, 380)
        window.transient(self.root)
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - 720) // 2
        )
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - 450) // 2
        )
        window.geometry(f"720x450+{x}+{y}")
        self._mirror_window = window
        self._mirror_rows.clear()
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="资产 URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(body, textvariable=self.mirror_url).grid(
            row=0, column=1, sticky="ew", padx=(8, 6)
        )
        self._mirror_button = ttk.Button(
            body, text="开始测速", command=self._start_mirror_test
        )
        self._mirror_button.grid(row=0, column=2)
        ttk.Label(
            body,
            text="并发探测前 256KB，按吞吐和首字节延迟评分；双击可用行可直接设为当前镜像。",
            foreground="#64748b",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 8))
        self._mirror_tree = ttk.Treeview(
            body,
            columns=("mirror", "speed", "latency", "score", "state"),
            show="headings",
            height=8,
        )
        for column, title, width in (
            ("mirror", "镜像", 140),
            ("speed", "速度", 100),
            ("latency", "延迟", 80),
            ("score", "评分", 80),
            ("state", "状态", 260),
        ):
            self._mirror_tree.heading(column, text=title)
            self._mirror_tree.column(column, width=width, anchor="w")
        self._mirror_tree.grid(
            row=2, column=0, columnspan=3, sticky="nsew"
        )
        self._mirror_tree.bind("<Double-1>", self._apply_selected_mirror)
        body.rowconfigure(2, weight=1)
        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._mirror_apply_button = ttk.Button(
            actions, text="设为当前镜像", command=self._apply_selected_mirror
        )
        self._mirror_apply_button.pack(side="left")
        ttk.Button(actions, text="关闭", command=self._close_mirror_dialog).pack(
            side="right"
        )
        window.protocol("WM_DELETE_WINDOW", self._close_mirror_dialog)

    def _close_mirror_dialog(self) -> None:
        window = self._mirror_window
        self._mirror_window = None
        self._mirror_tree = None
        self._mirror_button = None
        self._mirror_apply_button = None
        self._mirror_rows.clear()
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _start_mirror_test(self) -> None:
        if self._mirror_worker is not None and self._mirror_worker.is_alive():
            return
        url = self.mirror_url.get().strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showerror("URL 无效", "请输入 http/https 资产地址。")
            return
        if self._mirror_button is not None:
            self._mirror_button.configure(state="disabled")
        self.status.set("镜像测速中…")
        self._log_line(f"开始镜像测速：{url}")
        self._mirror_worker = threading.Thread(
            target=self._run_mirror_test, args=(url,), daemon=True
        )
        self._mirror_worker.start()

    def _run_mirror_test(self, url: str) -> None:
        try:
            # 测速弹窗始终测全部镜像，不受“固定使用某个镜像”影响。
            results = core.probe_mirrors(url, core.load_mirrors())
        except Exception as exc:  # noqa: BLE001
            self._queue.put(
                ("mirror_error", f"{type(exc).__name__}: {exc}")
            )
        else:
            self._queue.put(("mirror", results))

    def _show_mirrors(self, results: list[dict[str, Any]]) -> None:
        tree = self._mirror_tree
        if tree is not None:
            for row in tree.get_children():
                tree.delete(row)
        ordered = sorted(
            results,
            key=lambda item: (
                not bool(item.get("ok")),
                -float(item.get("score") or 0),
                -float(item.get("speed") or 0),
                float(item.get("latency") or 999),
            ),
        )
        self._mirror_rows.clear()
        for item in ordered:
            name = str(item.get("mirror", "?"))
            ok = bool(item.get("ok"))
            if ok:
                speed = f"{float(item.get('speed') or 0) / 1024:.1f} KB/s"
                latency = f"{float(item.get('latency') or 0):.2f}s"
                score = f"{float(item.get('score') or 0) / 1024:.1f}"
                state = "可用"
            else:
                speed = "-"
                latency = "-"
                score = "-"
                state = str(item.get("error") or "失败")[:120]
            if tree is not None:
                row = tree.insert(
                    "",
                    "end",
                    values=(name, speed, latency, score, state),
                )
                self._mirror_rows[row] = (name, ok)
        if self._mirror_button is not None:
            self._mirror_button.configure(state="normal")
        available = sum(1 for item in results if item.get("ok"))
        self.status.set(f"镜像测速完成：{available}/{len(results)} 可用")
        self._log_line(f"镜像测速完成：{available}/{len(results)} 可用")

    def _apply_selected_mirror(self, _event: tk.Event | None = None) -> None:
        tree = self._mirror_tree
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("选择镜像", "请先在列表中选择一个可用镜像。")
            return
        name, ok = self._mirror_rows.get(selection[0], ("", False))
        if not ok:
            messagebox.showerror("镜像不可用", "该镜像测速失败，不能设为当前镜像。")
            return
        self.github_mirror.set(name)
        self.status.set(f"已固定 GitHub 镜像：{name}（安装时生效）")
        self._log_line(f"已固定 GitHub 镜像：{name}")

    def _on_mirror_error(self, message: str) -> None:
        if self._mirror_button is not None:
            self._mirror_button.configure(state="normal")
        self.status.set("镜像测速失败")
        self._log_line("镜像测速失败：" + message)
        messagebox.showerror("测速失败", message[:2000])

    def _open_install_dir(self) -> None:
        if self._installed_dir is None:
            return
        target = self._installed_dir
        if not target.exists():
            messagebox.showerror("目录不存在", str(target))
            return
        try:
            _open_path(target)
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))

    def _launch_installed(self) -> None:
        self._launch_target(safe=False)

    def _launch_installed_safe(self) -> None:
        self._launch_target(safe=True)

    def _launch_target(self, *, safe: bool) -> None:
        if self._installed_dir is None:
            return
        target = core.launch_target(
            self._installed_dir, self._installed_frontend, safe=safe
        )
        if not target.is_file():
            messagebox.showerror("启动入口不存在", str(target))
            return
        try:
            _run_target(target, self._installed_dir)
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))

    def _on_close(self) -> None:
        if self._closing:
            return
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno(
                "任务进行中",
                "安装/卸载仍在进行，确定退出？\n"
                "安装器会先请求取消，并等待回滚和临时文件清理完成。",
            ):
                return
            self._closing = True
            self._close_deadline = time.monotonic() + 30.0
            self._cancel.set()
            self.cancel_button.configure(state="disabled")
            self.progress_step.set("正在取消并清理临时文件…")
            self.status.set("正在取消并清理…")
            self._log_line("正在等待后台任务结束并回滚…")
            self._wait_worker_then_close()
            return
        self._finish_close()

    def _wait_worker_then_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            if time.monotonic() < self._close_deadline:
                self.root.after(100, self._wait_worker_then_close)
                return
            self._log_line(
                "后台任务 30 秒内未退出，强制关闭；临时文件可能残留。"
            )
            self._finish_close(force=True)
            return
        self._finish_close()

    def _finish_close(self, force: bool = False) -> None:
        if (
            not force
            and self._worker is not None
            and self._worker.is_alive()
        ):
            return
        self._closing = True
        self._close_progress_dialog()
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None
        self.root.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="karenbot-installer", description="KarenBot 安装引导 GUI"
    )
    parser.add_argument(
        "--check", action="store_true", help="只检查 Tk 与资源是否可用"
    )
    parser.add_argument(
        "--smoke-test", action="store_true", help="打开窗口后自动退出（CI）"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="离线自检解压/校验/启动器（冻结版 CI）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        _print_console(
            json.dumps(
                {
                    "ok": _TK_ERROR is None,
                    "tk": getattr(tk, "TkVersion", None),
                    "python": sys.version.split()[0],
                    "default_install_dir": str(core.DEFAULT_INSTALL_DIR),
                    "installer_version": core.INSTALLER_VERSION,
                    "mirrors": len(core.load_mirrors()),
                    "gui_skins": list(core.GUI_SKINS),
                    "render_modes": list(core.RENDER_MODES),
                    "pypi_mirrors": list(core.PYPI_MIRRORS),
                    "windows_gui_asset": core.windows_gui_asset_name(),
                    "python_runtime_version": core.PYTHON_RUNTIME_VERSION,
                    "fonts": [item["name"] for item in core.FONT_SOURCES],
                    "tk_error": str(_TK_ERROR) if _TK_ERROR else "",
                },
                ensure_ascii=False,
            )
        )
        return 0 if _TK_ERROR is None else 2
    if args.self_test:
        try:
            core.self_test()
        except BaseException:  # noqa: BLE001 - 自检失败要打印完整原因
            _print_console(traceback.format_exc())
            return 1
        _print_console("self-test ok")
        return 0
    if _TK_ERROR is not None:
        _print_console(
            "缺少 Tkinter。Linux 请安装 python3-tk；"
            f"原始错误：{_TK_ERROR}"
        )
        return 2
    _enable_dpi_awareness()
    root = tk.Tk()
    try:
        InstallerApp(root)
    except Exception:  # noqa: BLE001
        text = traceback.format_exc()
        _print_console(text)
        try:
            messagebox.showerror("安装器启动失败", text[:2000])
        except Exception:  # noqa: BLE001
            pass
        root.destroy()
        return 1
    if args.smoke_test:
        root.after(500, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
