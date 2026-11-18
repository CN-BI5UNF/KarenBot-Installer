#!/usr/bin/env python3
"""用 gvsbuild GTK4 + python-build-standalone 组装 Windows 便携包。

在 windows-2022 runner 上运行。产物 KarenBot-Windows-x64.zip 包含
backend/ + gui/ + runtime/，由安装器 Release 提供下载。
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOP_LEVEL_IGNORE = (
    ".git",
    ".venv",
    "data",
    "build",
    "dist",
    "release",
    "module_backups",
    "backups",
)
RECURSIVE_IGNORE = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "*.log",
    ".karen-run",
    ".debata-run",
)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def copy_ignore(root: Path, *, top_level: bool = True):
    """顶层开发产物与递归缓存分开忽略，避免误删 site-packages/data。"""
    root_norm = os.path.normcase(os.path.abspath(str(root)))

    def _ignore(current: str, names: list[str]) -> set[str]:
        is_root = os.path.normcase(os.path.abspath(current)) == root_norm
        ignored: set[str] = set()
        for name in names:
            if top_level and is_root and _matches(name, TOP_LEVEL_IGNORE):
                ignored.add(name)
            elif _matches(name, RECURSIVE_IGNORE):
                ignored.add(name)
        return ignored

    return _ignore


def prune_bytecode(root: Path) -> None:
    """打包前删除 __pycache__/.pyc，避免长路径与无意义的包体膨胀。"""
    for current, dirnames, filenames in os.walk(root, topdown=True):
        for name in list(dirnames):
            if name == "__pycache__":
                shutil.rmtree(Path(current) / name, ignore_errors=True)
                dirnames.remove(name)
        for name in filenames:
            if name.casefold().endswith((".pyc", ".pyo")):
                try:
                    (Path(current) / name).unlink()
                except OSError:
                    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
    )


def copy_tree(source: Path, destination: Path, *, pattern: str = "*") -> None:
    if not source.is_dir():
        raise SystemExit(f"missing directory: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.glob(pattern):
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def extract_gvsbuild(archive: Path, destination: Path) -> Path:
    if not archive.is_file():
        raise SystemExit(f"gvsbuild archive not found: {archive}")
    print(f"extract gvsbuild: {archive} ({archive.stat().st_size} bytes)")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(destination)
    for candidate in (destination, destination / "gtk"):
        if (candidate / "bin").is_dir() and (candidate / "lib").is_dir():
            return candidate
    raise SystemExit("gvsbuild archive has no bin/ + lib/ root")


def extract_python(archive: Path, destination: Path, expected_sha256: str) -> Path:
    if not archive.is_file():
        raise SystemExit(f"python archive not found: {archive}")
    actual = sha256_file(archive)
    if actual.lower() != expected_sha256.lower():
        raise SystemExit(
            f"python archive sha256 mismatch: expected {expected_sha256}, got {actual}"
        )
    print(f"extract python: {archive} ({archive.stat().st_size} bytes)")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(destination)
    for candidate in (destination / "python", destination):
        if (candidate / "python.exe").is_file():
            return candidate
    raise SystemExit("python archive has no python.exe")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gvsbuild-zip", type=Path, required=True)
    parser.add_argument("--python-archive", type=Path, required=True)
    parser.add_argument(
        "--python-sha256",
        default="32b6cf90a03e2bc15fda8d2ab7ff78c4870f138887d612932650ce764efaab8f",
    )
    parser.add_argument("--backend-dir", type=Path, required=True)
    parser.add_argument("--gui-dir", type=Path, required=True)
    parser.add_argument(
        "--personas-dir",
        type=Path,
        required=True,
        help="KarenBot 主仓库的 personas/（backend 仓库本身不带人格文件）",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--backend-tag", default="unknown")
    parser.add_argument("--gui-tag", default="unknown")
    parser.add_argument("--personas-tag", default="main")
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args()

    work = (args.work_dir or (ROOT / "windows-bundle-work")).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    bundle = work / "KarenBot-Windows-x64"
    runtime = bundle / "runtime"
    bundle.mkdir(parents=True)

    try:
        gvsbuild = extract_gvsbuild(args.gvsbuild_zip.resolve(), work / "_gvsbuild")
        python_root = extract_python(
            args.python_archive.resolve(),
            work / "_python",
            args.python_sha256,
        )
        shutil.move(str(python_root), str(runtime))
        python = runtime / "python.exe"
        if not python.is_file():
            raise SystemExit("python.exe missing after extraction")

        print("copy application sources")
        backend = args.backend_dir.resolve()
        gui = args.gui_dir.resolve()
        if not (backend / "backend").is_dir():
            raise SystemExit(f"backend directory invalid: {backend}")
        if not (gui / "gui").is_dir():
            raise SystemExit(f"GUI directory invalid: {gui}")
        shutil.copytree(
            backend, bundle, dirs_exist_ok=True, ignore=copy_ignore(backend)
        )
        gui_target = bundle / "gui"
        if gui_target.exists():
            shutil.rmtree(gui_target)
        shutil.copytree(
            gui / "gui", gui_target, ignore=copy_ignore(gui / "gui")
        )
        icon = ROOT / "packaging" / "karenbot.ico"
        if icon.is_file():
            assets = gui_target / "assets"
            assets.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon, assets / "karenbot.ico")
            print("icon: gui/assets/karenbot.ico")

        print("copy personas from KarenBot main repository")
        personas = args.personas_dir.resolve()
        persona_dirs = [
            item
            for item in personas.iterdir()
            if item.is_dir() and (item / "persona_prompt.py").is_file()
        ]
        if not persona_dirs:
            raise SystemExit(
                f"personas directory has no persona_prompt.py: {personas}"
            )
        personas_target = bundle / "personas"
        if personas_target.exists():
            shutil.rmtree(personas_target)
        shutil.copytree(
            personas,
            personas_target,
            ignore=copy_ignore(personas, top_level=False),
        )
        names = ", ".join(sorted(item.name for item in persona_dirs))
        # GitHub Actions 的 Windows 控制台默认 cp1252；中文人格名直接
        # print 会抛 UnicodeEncodeError，这里转成 ASCII 转义只用于日志。
        print("personas: " + names.encode("ascii", "backslashreplace").decode("ascii"))

        print("copy GTK resources from gvsbuild")
        runtime_bin = runtime / "bin"
        runtime_lib = runtime / "lib"
        runtime_share = runtime / "share"
        runtime_etc = runtime / "etc"
        runtime_bin.mkdir(parents=True, exist_ok=True)
        runtime_lib.mkdir(parents=True, exist_ok=True)
        runtime_share.mkdir(parents=True, exist_ok=True)
        runtime_etc.mkdir(parents=True, exist_ok=True)
        for dll in (gvsbuild / "bin").glob("*.dll"):
            shutil.copy2(dll, runtime_bin / dll.name)
        for exe in (
            "gdk-pixbuf-query-loaders.exe",
            "glib-compile-schemas.exe",
            "gspawn-win64-helper.exe",
            "gspawn-win64-helper-console.exe",
        ):
            source = gvsbuild / "bin" / exe
            if source.is_file():
                shutil.copy2(source, runtime_bin / exe)
        copy_tree(gvsbuild / "lib" / "girepository-1.0", runtime_lib / "girepository-1.0")
        copy_tree(gvsbuild / "lib" / "gdk-pixbuf-2.0", runtime_lib / "gdk-pixbuf-2.0")
        if (gvsbuild / "lib" / "gio").is_dir():
            copy_tree(gvsbuild / "lib" / "gio", runtime_lib / "gio")
        for name in ("glib-2.0", "icons", "gtk-4.0", "fontconfig"):
            source = gvsbuild / "share" / name
            if source.is_dir():
                copy_tree(source, runtime_share / name)
        if (gvsbuild / "etc" / "fonts").is_dir():
            copy_tree(gvsbuild / "etc" / "fonts", runtime_etc / "fonts")

        print("copy Visual C++ runtime DLLs")
        system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
        for dll in (
            "vcruntime140.dll",
            "vcruntime140_1.dll",
            "msvcp140.dll",
            "msvcp140_1.dll",
            "msvcp140_2.dll",
            "concrt140.dll",
            "vcomp140.dll",
        ):
            source = system32 / dll
            if source.is_file():
                shutil.copy2(source, runtime_bin / dll)

        env = os.environ.copy()
        env["PYTHONNOUSERSITE"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

        wheels = gvsbuild / "wheels"
        print("install gvsbuild PyGObject/PyCairo wheels")
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheels),
                "--force-reinstall",
                "--no-warn-script-location",
                "pygobject",
                "pycairo",
            ],
            env=env,
        )

        print("install KarenBot backend and dependencies")
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-input",
                "--no-warn-script-location",
                str(args.backend_dir.resolve()),
            ],
            env=env,
        )

        manifest = {
            "schema": 1,
            "backend": {
                "repo": "CN-BI5UNF/KarenBot-backend",
                "tag": args.backend_tag,
            },
            "gui": {
                "repo": "CN-BI5UNF/KarenBot-GUI",
                "tag": args.gui_tag,
            },
            "personas": {
                "repo": "CN-BI5UNF/KarenBot",
                "ref": args.personas_tag,
            },
            "runtime": {
                "kind": "windows-gtk4-portable",
                "python": "3.14.10",
                "gtk_source": "gvsbuild-2026.8.0",
            },
        }
        (bundle / "windows-bundle.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        print("generate gdk-pixbuf loaders cache")
        module_dir = runtime_lib / "gdk-pixbuf-2.0" / "2.10.0" / "loaders"
        cache = module_dir.parent / "loaders.cache"
        query = runtime_bin / "gdk-pixbuf-query-loaders.exe"
        if query.is_file() and module_dir.is_dir():
            smoke_env = env.copy()
            smoke_env["PATH"] = os.pathsep.join(
                [str(runtime_bin), str(runtime), smoke_env.get("PATH", "")]
            )
            smoke_env["GDK_PIXBUF_MODULEDIR"] = str(module_dir)
            result = subprocess.run(
                [str(query)],
                capture_output=True,
                text=True,
                env=smoke_env,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                cache.write_text(result.stdout, encoding="utf-8")
                print(f"loaders cache: {cache}")
            else:
                print(f"warning: loaders cache failed: {result.stderr[:500]}")

        print("smoke test")
        smoke_env = env.copy()
        smoke_env["PATH"] = os.pathsep.join(
            [str(runtime), str(runtime_bin), smoke_env.get("PATH", "")]
        )
        smoke_env["XDG_DATA_DIRS"] = str(runtime_share)
        smoke_env["GI_TYPELIB_PATH"] = str(runtime_lib / "girepository-1.0")
        smoke_env["GSETTINGS_SCHEMA_DIR"] = str(
            runtime_share / "glib-2.0" / "schemas"
        )
        smoke_env["GTK_DATA_PREFIX"] = str(runtime)
        smoke_env["GTK_EXE_PREFIX"] = str(runtime)
        smoke_env["GDK_PIXBUF_MODULEDIR"] = str(module_dir)
        smoke_env["GDK_PIXBUF_MODULE_FILE"] = str(cache)
        fonts_conf = runtime_etc / "fonts" / "fonts.conf"
        if fonts_conf.is_file():
            smoke_env["FONTCONFIG_FILE"] = str(fonts_conf)
        run(
            [
                str(python),
                "-c",
                "import gi; gi.require_version('Gtk','4.0'); "
                "gi.require_version('Adw','1'); from gi.repository import Gtk, Adw; "
                "print('GTK', Gtk.get_major_version(), Gtk.get_minor_version(), "
                "'Adw', Adw.get_major_version())",
            ],
            env=smoke_env,
        )
        run(
            [
                str(python),
                "-c",
                "import fastapi, httpx, pydantic, orjson, gi; "
                "print('backend deps ok')",
            ],
            env=smoke_env,
        )
        run([str(python), "-m", "gui", "--version"], cwd=bundle, env=smoke_env)
        run(
            [
                str(python),
                "-c",
                "from pathlib import Path; p=Path('personas'); "
                "names=[x.name for x in p.iterdir() "
                "if x.is_dir() and (x/'persona_prompt.py').is_file()]; "
                "assert names, 'no persona_prompt.py'; "
                "print('personas ok:', len(names), "
                "ascii(sorted(names)))",
            ],
            cwd=bundle,
            env=smoke_env,
        )

        print("prune bytecode caches")
        prune_bytecode(bundle)
        print("create archive")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        archive_base = args.out.with_suffix("")
        if args.out.exists():
            args.out.unlink()
        shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=str(work),
            base_dir="KarenBot-Windows-x64",
        )
        print(
            f"bundle: {args.out} "
            f"({args.out.stat().st_size / 1024 / 1024:.1f} MiB)"
        )
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
