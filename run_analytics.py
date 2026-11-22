#!/usr/bin/env python3
"""命令行启动脚本（兼容 python -I 隔离模式）。

隔离模式不会把当前目录加入 sys.path，因此这里显式加入 src/，
保证只依赖标准库、不读取任何环境中的第三方包路径。
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from renewal_analytics.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
