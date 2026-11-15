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
import threading
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
DEFAULT_PROBE_URL = (
    "https://github.com/CN-BI5UNF/KarenBot-backend/archive/refs/tags/"
    "v0.1.1-alpha.1.tar.gz"
)


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
        height = min(720, max(620, screen_height - 80))
        x = max(0, (screen_width - width) // 2)
        y = max(0, min(20, screen_height - height - 60))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(820, 620)
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
        self._cancel = threading.Event()
        self._installed_dir: Path | None = None
        self._installed_frontend = "gui"
        self._delete_on_close = False
        self._skin_radios: list[ttk.Radiobutton] = []
        self._render_radios: list[ttk.Radiobutton] = []
        self._build_vars()
        self._build_ui()
        self._update_source_state()
        self._update_appearance_state()
        self._poll_queue()

    def _build_vars(self) -> None:
        self.source_mode = tk.StringVar(value="online")
        self.source_path = tk.StringVar()
        self.archive_path = tk.StringVar()
        self.frontend = tk.StringVar(value="gui")
        self.install_dir = tk.StringVar(value=str(core.DEFAULT_INSTALL_DIR))
        self.create_shortcut = tk.BooleanVar(value=True)
        self.download_fonts = tk.BooleanVar(value=True)
        self.self_delete = tk.BooleanVar(
            value=bool(getattr(sys, "frozen", False))
        )
        self.gui_skin = tk.StringVar(value="karen")
        self.render_mode = tk.StringVar(value="auto")
        self.mirror_url = tk.StringVar(value=DEFAULT_PROBE_URL)
        self.status = tk.StringVar(value="就绪")
        self.progress_step = tk.StringVar(value="准备安装…")
        self.skin_note = tk.StringVar(value="")

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg="#111827", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header,
            text=APP_TITLE,
            bg="#111827",
            fg="#f9fafb",
            font=(self.font_family, 16, "bold"),
        ).pack(anchor="w", padx=18, pady=(8, 0))
        tk.Label(
            header,
            text="选择来源、界面和外观；安装器本身不内置应用包。",
            bg="#111827",
            fg="#cbd5e1",
            font=(self.font_family, 9),
        ).pack(anchor="w", padx=18, pady=(1, 0))

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
        self._build_options_section(self.config_inner)
        self._build_appearance_section(self.config_inner)

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

    def _build_source_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="1. 安装来源", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        frame.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            frame,
            text="在线安装（自动选择稳定版/最新版）",
            variable=self.source_mode,
            value="online",
            command=self._update_source_state,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Radiobutton(
            frame,
            text="本地源码目录",
            variable=self.source_mode,
            value="source",
            command=self._update_source_state,
        ).grid(row=1, column=0, sticky="w", pady=2)
        self.source_entry = ttk.Entry(frame, textvariable=self.source_path)
        self.source_entry.grid(row=1, column=1, sticky="ew", padx=(8, 6), pady=2)
        self.source_button = ttk.Button(
            frame, text="浏览…", command=self._browse_source
        )
        self.source_button.grid(row=1, column=2, pady=2)

        ttk.Radiobutton(
            frame,
            text="本地安装包",
            variable=self.source_mode,
            value="archive",
            command=self._update_source_state,
        ).grid(row=2, column=0, sticky="w", pady=2)
        self.archive_entry = ttk.Entry(frame, textvariable=self.archive_path)
        self.archive_entry.grid(row=2, column=1, sticky="ew", padx=(8, 6), pady=2)
        self.archive_button = ttk.Button(
            frame, text="浏览…", command=self._browse_archive
        )
        self.archive_button.grid(row=2, column=2, pady=2)

        ttk.Label(
            frame,
            text=(
                "在线安装会下载 backend 核心包和 GUI/WebUI 模块；"
                "Windows 原生 GUI 需要安装器 Release 的 GTK4 便携预构建包。"
            ),
            foreground="#64748b",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def _build_frontend_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="2. 界面版本", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Radiobutton(
            row,
            text="原生 GUI（GTK4，推荐桌面）",
            variable=self.frontend,
            value="gui",
            command=self._update_appearance_state,
        ).pack(side="left")
        ttk.Radiobutton(
            row,
            text="WebUI（浏览器控制台）",
            variable=self.frontend,
            value="webui",
            command=self._update_appearance_state,
        ).pack(side="left", padx=(18, 0))
        ttk.Label(
            frame,
            text=(
                "GUI 需要 GTK4（Windows 用便携预构建包）；"
                "WebUI 走浏览器，无需 GTK，文件以浏览器下载/内嵌打开为主。"
            ),
            foreground="#64748b",
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

    def _build_install_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="3. 安装位置", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.install_dir).grid(
            row=0, column=1, sticky="ew", padx=(8, 6)
        )
        ttk.Button(frame, text="浏览…", command=self._browse_install_dir).grid(
            row=0, column=2
        )
        ttk.Label(
            frame,
            text=(
                "默认目录在用户 AppData，通常不需要管理员权限；"
                "Windows 路径请不要包含中文或其他非 ASCII 字符。"
            ),
            foreground="#64748b",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))

    def _build_options_section(self, body: ttk.Frame) -> None:
        frame = ttk.LabelFrame(body, text="4. 选项", padding=5)
        frame.pack(fill="x", pady=(0, 3))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Checkbutton(
            row,
            text="创建快捷方式",
            variable=self.create_shortcut,
        ).pack(side="left")
        ttk.Checkbutton(
            row,
            text="下载中文字体（Noto Sans/Serif SC，自动拉取）",
            variable=self.download_fonts,
        ).pack(side="left", padx=(14, 0))
        delete = ttk.Checkbutton(
            row,
            text="完成后删除安装器",
            variable=self.self_delete,
        )
        if not getattr(sys, "frozen", False):
            delete.configure(state="disabled")
            self.self_delete.set(False)
        delete.pack(side="left", padx=(14, 0))

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

        self.skin_note_label = ttk.Label(
            frame, textvariable=self.skin_note, foreground="#b45309"
        )
        self.skin_note_label.pack(anchor="w", pady=(3, 2))

        ttk.Label(
            frame,
            text=(
                "自动模式由 GUI 启动器在初始化 GTK 前按 stage 回退："
                "GSK_RENDERER=cairo / GDK_DEBUG=gl-disable / LIBGL_ALWAYS_SOFTWARE=1。\n"
                "仍黑屏时可用“兼容模式启动”或桌面上的“KarenBot GUI (兼容模式)”。"
            ),
            foreground="#64748b",
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

    def _update_source_state(self) -> None:
        mode = self.source_mode.get()
        source_enabled = mode == "source"
        archive_enabled = mode == "archive"
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
        enabled = self.frontend.get() == "gui"
        state = "normal" if enabled else "disabled"
        for radio in self._skin_radios:
            radio.configure(state=state)
        for radio in self._render_radios:
            radio.configure(state=state)
        self._update_skin_note()

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

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")
        if self._mirror_button is not None:
            self._mirror_button.configure(
                state="disabled" if running else "normal"
            )
        self._update_appearance_state()
        if running:
            for radio in self._skin_radios + self._render_radios:
                radio.configure(state="disabled")

    def _queue_log(self, line: str) -> None:
        self._queue.put(("log", line))

    def _queue_progress(self, done: int, total: int | None) -> None:
        self._queue.put(("progress", (done, total)))

    def _queue_status(self, message: str) -> None:
        self._queue.put(("stage", message))

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

        self._installed_dir = None
        self._installed_frontend = frontend
        self._delete_on_close = False
        self._cancel.clear()
        self._set_running(True)
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.progress_step.set("准备安装…")
        self.status.set("准备安装…")
        self._open_progress_dialog()
        self._clear_log()
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
            f"渲染：{self.render_mode.get()}"
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
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
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
        self.root.after(80, self._poll_queue)

    def _on_stage(self, message: str) -> None:
        self.progress_step.set(message)
        self.status.set(message)

    def _update_progress(self, done: int, total: int | None) -> None:
        dialog = self._dialog_progress
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
            self.status.set(
                f"下载中 {done / 1024 / 1024:.1f} / "
                f"{total / 1024 / 1024:.1f} MB"
            )
            return
        if str(self.progress.cget("mode")) != "indeterminate":
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        if dialog is not None and str(dialog.cget("mode")) != "indeterminate":
            dialog.configure(mode="indeterminate")
            dialog.start(12)
        self.status.set(f"已下载 {done / 1024 / 1024:.1f} MB")

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
        self._log_line(f"安装完成：{path}")
        if self.self_delete.get() and getattr(sys, "frozen", False):
            self._delete_on_close = True
            self._log_line("关闭安装器窗口后会自删除。")
        target = core.launch_target(path, self._installed_frontend)
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
        messagebox.showerror("安装失败", message[:2000])

    def _on_cancelled(self) -> None:
        self._worker = None
        self._close_progress_dialog()
        self._set_running(False)
        self.progress.stop()
        self.status.set("已取消")
        self._log_line("安装已取消。")

    def _cancel_install(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            return
        self._cancel.set()
        self.cancel_button.configure(state="disabled")
        self.progress_step.set("正在取消…")
        self.status.set("正在取消…")
        self._log_line("已请求取消；正在等待当前步骤结束。")

    def _open_progress_dialog(self) -> None:
        if self._progress_window is not None:
            try:
                if self._progress_window.winfo_exists():
                    self._progress_window.lift()
                    return
            except tk.TclError:
                pass
            self._progress_window = None
        window = tk.Toplevel(self.root)
        window.title("正在安装 KarenBot")
        window.transient(self.root)
        window.resizable(False, False)
        self.root.update_idletasks()
        width = 540
        height = 230
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - width) // 2
        )
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - height) // 2
        )
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.protocol("WM_DELETE_WINDOW", self._cancel_install)
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="正在安装 KarenBot",
            font=(self.font_family, 13, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            textvariable=self.progress_step,
            wraplength=480,
            justify="left",
        ).pack(anchor="w", pady=(8, 10))
        self._dialog_progress = ttk.Progressbar(
            body, mode="indeterminate", length=480
        )
        self._dialog_progress.pack(fill="x")
        self._dialog_progress.start(12)
        ttk.Label(
            body,
            text=(
                "安装器会自动测速 GitHub 镜像，不需要先手动点“镜像测速”。\n"
                "如果长时间无响应，请检查网络、代理、DNS 或防火墙。"
            ),
            foreground="#64748b",
            justify="left",
            wraplength=480,
        ).pack(anchor="w", pady=(10, 12))
        ttk.Button(
            body, text="取消安装", command=self._cancel_install
        ).pack(anchor="e")
        self._progress_window = window
        try:
            window.grab_set()
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
            text="并发探测前 512KB，按吞吐和首字节延迟评分；下载失败会自动换镜像。",
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
        body.rowconfigure(2, weight=1)
        ttk.Button(body, text="关闭", command=self._close_mirror_dialog).grid(
            row=3, column=2, sticky="e", pady=(8, 0)
        )
        window.protocol("WM_DELETE_WINDOW", self._close_mirror_dialog)

    def _close_mirror_dialog(self) -> None:
        window = self._mirror_window
        self._mirror_window = None
        self._mirror_tree = None
        self._mirror_button = None
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
            results = core.probe_mirrors(url, core.available_mirrors())
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
        for item in ordered:
            if item.get("ok"):
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
                tree.insert(
                    "",
                    "end",
                    values=(
                        item.get("mirror", "?"),
                        speed,
                        latency,
                        score,
                        state,
                    ),
                )
        if self._mirror_button is not None:
            self._mirror_button.configure(state="normal")
        available = sum(1 for item in results if item.get("ok"))
        self.status.set(f"镜像测速完成：{available}/{len(results)} 可用")
        self._log_line(f"镜像测速完成：{available}/{len(results)} 可用")

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
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno(
                "安装进行中",
                "安装仍在进行，确定退出？未完成的临时文件会保留。",
            ):
                return
            self._cancel.set()
        self._close_progress_dialog()
        if self._delete_on_close:
            core.self_delete_installer()
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
                    "windows_gui_asset": core.windows_gui_asset_name(),
                    "python_runtime_version": core.PYTHON_RUNTIME_VERSION,
                    "bundled_python": core.has_bundled_python(),
                    "fonts": [item["name"] for item in core.FONT_SOURCES],
                    "tk_error": str(_TK_ERROR) if _TK_ERROR else "",
                },
                ensure_ascii=False,
            )
        )
        return 0 if _TK_ERROR is None else 2
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
