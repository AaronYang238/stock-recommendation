"""AI 工厂：get_analyzer(config) 按 provider 返回实现。

降级规则（第 4.4 节，强制）：
  - enabled=false / provider=none / Key 缺失 / SDK 未装 / 构造异常 → NullAnalyzer
保证业务侧拿到的永远是一个可用的 AIAnalyzer，绝不因 AI 而崩。
"""
from __future__ import annotations

import logging

from ..config import AIConfig, Config
from .base import AIAnalyzer
from .null_analyzer import NullAnalyzer

log = logging.getLogger(__name__)


def get_analyzer(config: Config | AIConfig) -> AIAnalyzer:
    cfg = config.ai if isinstance(config, Config) else config

    if not cfg.enabled or cfg.provider in (None, "none", ""):
        return NullAnalyzer()

    # 除本地模型外，云端提供商必须有 Key，否则降级
    if cfg.provider in ("anthropic", "openai") and not cfg.api_key:
        log.warning("provider=%s 但环境变量 %s 未设置，降级 NullAnalyzer",
                    cfg.provider, cfg.api_key_env)
        return NullAnalyzer()

    try:
        if cfg.provider == "anthropic":
            from .anthropic_analyzer import AnthropicAnalyzer
            return AnthropicAnalyzer(cfg)
        if cfg.provider == "openai":
            from .openai_analyzer import OpenAIAnalyzer
            return OpenAIAnalyzer(cfg)
        if cfg.provider == "local":
            from .openai_analyzer import LocalAnalyzer
            return LocalAnalyzer(cfg)
        log.warning("未知 provider=%s，降级 NullAnalyzer", cfg.provider)
        return NullAnalyzer()
    except Exception as e:  # noqa: BLE001 — SDK 未装/初始化失败一律降级
        log.warning("AI 适配器构造失败(%s)，降级 NullAnalyzer", e)
        return NullAnalyzer()
