#!/usr/bin/env python
"""Django 管理入口。"""
import os
import sys
from pathlib import Path

# 让后端能 import 仓库根 src/ 下的确定性核心包 aselect
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aselect_api.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "未找到 Django。请先 `pip install django djangorestframework`。"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
