"""配置加载：YAML 文件 + 环境变量。

Key 一律从环境变量读取，禁止从配置文件 / 代码中取真实密钥（铁律二、第 6 节安全）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 项目根目录（src/aselect/config.py -> 上溯两级到包，再上一级到 src，再一级到根）
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT / "config" / "config.example.yaml"


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    provider: str = "none"
    model: str = "claude-haiku-4-5"
    api_key_env: str = "AI_API_KEY"
    base_url: str | None = None
    timeout_s: int = 30
    cache_dir: str = ".cache/ai"
    features: dict[str, bool] = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        """密钥只从环境变量读取。"""
        return os.environ.get(self.api_key_env) or None

    def feature_on(self, name: str) -> bool:
        """某接入点是否启用：需全局 enabled 且该 feature 未被单独关闭。"""
        return bool(self.enabled) and bool(self.features.get(name, False))


@dataclass(frozen=True)
class Config:
    app: dict[str, Any]
    datasource: dict[str, Any]
    storage: dict[str, Any]
    backtest: dict[str, Any]
    ai: AIConfig
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def disclaimer(self) -> str:
        return self.app.get(
            "disclaimer",
            "本系统仅供研究，不构成投资建议；盈亏自负。",
        )


def load_config(path: str | os.PathLike | None = None) -> Config:
    """加载配置；找不到 config.yaml 时回退到 config.example.yaml。"""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        p = EXAMPLE_CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # 相对路径一律按仓库根解析，确保从任意 CWD（如 backend/）启动都读到同一份数据
    storage = raw.get("storage", {}) or {}
    for key in ("path", "parquet_dir"):
        if storage.get(key):
            storage[key] = _abs_under_root(storage[key])

    ai_raw = raw.get("ai", {}) or {}
    ai = AIConfig(
        enabled=bool(ai_raw.get("enabled", False)),
        provider=str(ai_raw.get("provider", "none")),
        model=str(ai_raw.get("model", "claude-haiku-4-5")),
        api_key_env=str(ai_raw.get("api_key_env", "AI_API_KEY")),
        base_url=ai_raw.get("base_url") or None,
        timeout_s=int(ai_raw.get("timeout_s", 30)),
        cache_dir=_abs_under_root(ai_raw.get("cache_dir", ".cache/ai")),
        features=dict(ai_raw.get("features", {}) or {}),
    )
    return Config(
        app=raw.get("app", {}) or {},
        datasource=raw.get("datasource", {}) or {},
        storage=storage,
        backtest=raw.get("backtest", {}) or {},
        ai=ai,
        raw=raw,
    )


def _abs_under_root(path: str) -> str:
    """相对路径解析到仓库根；绝对路径原样返回。"""
    p = Path(path)
    return str(p if p.is_absolute() else (ROOT / p))
