from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(BASH is None, reason="bash is required")
def test_piped_bootstrap_ignores_working_directory_installer(tmp_path):
    work = tmp_path / "work"
    fake_bin = tmp_path / "bin"
    work.mkdir()
    fake_bin.mkdir()
    (work / "karen_install.py").write_text(
        "raise SystemExit('working directory source executed')\n",
        encoding="utf-8",
    )
    capture = tmp_path / "python-args"
    _write_executable(
        fake_bin / "python3",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\nexit 0\n",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
out=""
for arg in "$@"; do
    if [ "$next" = "yes" ]; then out="$arg"; next=""; continue; fi
    if [ "$arg" = "-o" ]; then next="yes"; fi
done
[ -n "$out" ] || exit 2
printf 'downloaded core\\n' > "$out"
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "CAPTURE": str(capture),
            "KAREN_INSTALLER_REPO": "owner/repo",
            "TMPDIR": str(tmp_path),
        }
    )

    result = subprocess.run(
        [BASH, "-s", "--", "--check"],
        input=(ROOT / "install.sh").read_bytes(),
        cwd=work,
        env=env,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    args = capture.read_text(encoding="utf-8").splitlines()
    assert args[0].startswith(str(tmp_path / "karen-installer."))
    assert args[0] != str(work / "karen_install.py")
    assert args[1] == "--check"


@pytest.mark.skipif(BASH is None, reason="bash is required")
def test_piped_bootstrap_rejects_unverified_proxy_core(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "python-args"
    _write_executable(
        fake_bin / "python3",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\nexit 0\n",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
out=""
url=""
next=""
for arg in "$@"; do
    if [ "$next" = "yes" ]; then out="$arg"; next=""; continue; fi
    if [ "$arg" = "-o" ]; then next="yes"; continue; fi
    case "$arg" in https://*) url="$arg" ;; esac
done
case "$url" in
    https://raw.githubusercontent.com/*) exit 22 ;;
esac
printf 'untrusted core\\n' > "$out"
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "CAPTURE": str(capture),
            "KAREN_INSTALLER_REPO": "owner/repo",
        }
    )

    result = subprocess.run(
        [BASH, "-s"],
        input=(ROOT / "install.sh").read_bytes(),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "拒绝从第三方镜像运行未经校验".encode() in result.stderr
    assert not capture.exists()


@pytest.mark.skipif(BASH is None, reason="bash is required")
def test_bootstrap_hash_comparison_accepts_uppercase_digest(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "python-args"
    _write_executable(
        fake_bin / "python3",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\nexit 0\n",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
out=""
next=""
for arg in "$@"; do
    if [ "$next" = "yes" ]; then out="$arg"; next=""; continue; fi
    if [ "$arg" = "-o" ]; then next="yes"; fi
done
printf 'trusted core\\n' > "$out"
""",
    )
    expected = hashlib.sha256(b"trusted core\n").hexdigest().upper()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "CAPTURE": str(capture),
            "KAREN_INSTALLER_REPO": "owner/repo",
            "KAREN_INSTALLER_SHA256": expected,
        }
    )

    result = subprocess.run(
        [BASH, "-s", "--", "--check"],
        input=(ROOT / "install.sh").read_bytes(),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert capture.exists()
