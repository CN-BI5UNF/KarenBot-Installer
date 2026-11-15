"""让测试可以直接导入仓库根目录的安装器模块。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karen_install  # noqa: E402


@pytest.fixture(autouse=True)
def _posix_installer_defaults(monkeypatch):
    """测试默认按 Linux 路径运行，平台相关用例自行覆盖 IS_WINDOWS。"""
    monkeypatch.setattr(karen_install, "IS_WINDOWS", False)
    karen_install.reset_runtime_caches()
