"""铁律一：确定性核心(engine 包)中不得出现任何 LLM 调用。

静态检查：扫描 engine/ 下所有源码，禁止 import anthropic/openai，
也禁止 import aselect.ai（核心不得依赖 AI 模块）。
对应验收：「核心代码中无任何对 LLM 的调用」。
"""
from __future__ import annotations

import re
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "aselect" / "engine"

FORBIDDEN = [
    re.compile(r"\bimport\s+anthropic\b"),
    re.compile(r"\bimport\s+openai\b"),
    re.compile(r"\bfrom\s+anthropic\b"),
    re.compile(r"\bfrom\s+openai\b"),
    re.compile(r"\bfrom\s+\.\.ai\b"),          # from ..ai import ...
    re.compile(r"\bimport\s+aselect\.ai\b"),
    re.compile(r"\baselect\.ai\b"),
]


def test_engine_has_no_llm_imports():
    offenders = []
    for py in ENGINE_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            if pat.search(text):
                offenders.append((py.name, pat.pattern))
    assert not offenders, f"引擎层出现 AI/LLM 依赖: {offenders}"
