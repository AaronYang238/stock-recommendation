"""OpenAI 兼容适配器。base_url 可指向自托管/兼容 OpenAI 协议的本地模型。"""
from __future__ import annotations

from ..config import AIConfig
from .llm_base import LLMAnalyzer


class OpenAIAnalyzer(LLMAnalyzer):
    def __init__(self, cfg: AIConfig):
        super().__init__(cfg)
        from openai import OpenAI  # 延迟导入
        kwargs = {"api_key": cfg.api_key, "timeout": cfg.timeout_s}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self.client = OpenAI(**kwargs)
        self.model = cfg.model

    def _chat(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


# 本地模型：复用 OpenAI 兼容协议（vLLM / Ollama / LM Studio 等），仅 base_url 不同。
class LocalAnalyzer(OpenAIAnalyzer):
    pass
