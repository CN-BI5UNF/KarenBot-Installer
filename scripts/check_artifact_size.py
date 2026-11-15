#!/usr/bin/env python3
"""校验安装器成品大小不超过给定 MiB（默认 50）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-mib", type=float, default=50.0)
    args = parser.parse_args()
    if not args.path.is_file():
        print(f"artifact not found: {args.path}")
        return 1
    size = args.path.stat().st_size
    limit = int(args.max_mib * 1024 * 1024)
    print(
        f"{args.path}: {size / 1024 / 1024:.2f} MiB "
        f"(limit {args.max_mib:.2f} MiB)"
    )
    if size > limit:
        print("artifact exceeds size limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
