"""存储工厂：按配置返回实现，便于替换后端。"""
from __future__ import annotations

from ..config import Config
from .base import Storage
from .sqlite_store import SQLiteStorage


def get_storage(config: Config) -> Storage:
    backend = config.storage.get("backend", "sqlite")
    if backend == "sqlite":
        return SQLiteStorage(config.storage.get("path", "data_store/aselect.sqlite"))
    raise NotImplementedError(f"未实现的存储后端: {backend}")
