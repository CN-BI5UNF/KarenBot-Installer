#!/usr/bin/env python3
"""下载 Windows 安装器内置的精简 Python，并写入 vendor/ 与校验文件。

只在 Windows 构建 job 中调用。安装器成品会因此增加约 22 MB，仍低于 50 MiB。
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karen_install as core  # noqa: E402


def main() -> int:
    source = core.BUNDLED_PYTHON_SOURCES.get("windows-x64")
    if not source:
        print("no bundled Python source for windows-x64", file=sys.stderr)
        return 1
    expected = str(source["sha256"]).lower()
    vendor = ROOT / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    archive = vendor / core.BUNDLED_PYTHON_ARCHIVE_NAME
    hash_file = vendor / core.BUNDLED_PYTHON_HASH_NAME

    if archive.is_file():
        actual = core.sha256_file(archive)
        if actual.lower() == expected:
            print(f"reuse bundled Python: {archive} ({archive.stat().st_size} bytes)")
            hash_file.write_text(f"{expected}  {archive.name}\n", encoding="ascii")
            return 0
        archive.unlink()

    temp = archive.with_suffix(archive.suffix + ".part")
    print(f"download bundled Python: {source['url']}")
    request = urllib.request.Request(
        str(source["url"]), headers={"User-Agent": core.USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            with temp.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        actual = core.sha256_file(temp)
        if actual.lower() != expected:
            temp.unlink(missing_ok=True)
            print(
                f"sha256 mismatch: expected {expected}, got {actual}",
                file=sys.stderr,
            )
            return 1
        temp.replace(archive)
    except Exception as exc:  # noqa: BLE001
        temp.unlink(missing_ok=True)
        print(f"download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    hash_file.write_text(f"{expected}  {archive.name}\n", encoding="ascii")
    print(f"saved {archive} ({archive.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
