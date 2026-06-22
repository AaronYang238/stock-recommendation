"""AI 调用缓存（第 4.6 节成本控制：同文本不重复请求）。

基于内容哈希的本地 JSON 文件缓存，跨进程持久。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class JSONCache:
    def __init__(self, cache_dir: str):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: Any) -> str:
        blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str):
        f = self.dir / f"{key}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return None

    def set(self, key: str, value) -> None:
        (self.dir / f"{key}.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
