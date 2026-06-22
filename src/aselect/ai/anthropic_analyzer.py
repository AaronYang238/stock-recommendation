"""Anthropic 适配器。延迟导入 anthropic SDK；未装/无 Key 时由工厂改用 NullAnalyzer。"""
from __future__ import annotations

from ..config import AIConfig
from .llm_base import LLMAnalyzer


class AnthropicAnalyzer(LLMAnalyzer):
    def __init__(self, cfg: AIConfig):
        super().__init__(cfg)
        import anthropic  # 延迟导入
        kwargs = {"api_key": cfg.api_key, "timeout": cfg.timeout_s}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = cfg.model

    def _chat(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # 拼接所有文本块
        return "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )
