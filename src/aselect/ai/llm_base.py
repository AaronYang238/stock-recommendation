"""LLM 适配器公共基类：负责缓存、feature 开关、RAG prompt 构造、JSON 解析。

具体提供商只需实现 `_chat(system, user) -> str`。这样新增提供商只写薄薄一层。
所有方法在出错/超时时退化为中性结果，绝不向核心抛异常（鲁棒降级）。
"""
from __future__ import annotations

import json
import logging

from ..config import AIConfig
from .base import (
    AIAnalyzer, SentimentResult, EventResult, FilterSpecDTO, ReportResult,
)
from .cache import JSONCache

log = logging.getLogger(__name__)

_SENTIMENT_SYS = (
    "你是金融文本情绪分析器。只输出 JSON 数组，每条形如 "
    '{"sentiment": <-1到1的小数>, "confidence": <0到1>}，'
    "与输入文本一一对应。不要输出任何额外文字。"
)
_EVENT_SYS = (
    "你是金融事件抽取器。只输出 JSON 数组，每条形如 "
    '{"ticker": "<代码或空>", "event_type": "<事件类型>", "confidence": <0到1>}。'
    "无明确事件则该项 event_type 为空字符串。不要输出额外文字。"
)
_NL_FILTER_SYS = (
    "你把自然语言选股指令转成受控筛选条件 JSON，"
    "形如 {\"name\":..., \"logic\":\"and|or\", \"conditions\":"
    "[{\"field\":..., \"op\":\"<|<=|>|>=|==|!=\", \"value\":...}], "
    "\"sort_by\":..., \"ascending\":bool, \"limit\":int}。"
    "field 只能取给定 schema 中的字段；严禁输出任何代码或解释，只输出 JSON。"
)
_REPORT_SYS = (
    "你是严谨的证券研究助理。只能基于用户提供的结构化数据撰写分析，"
    "严禁补充或编造任何数字、价格、财务指标。若数据不足，明确说明数据不足。"
    "结尾附一句风险提示。"
)


class LLMAnalyzer(AIAnalyzer):
    def __init__(self, cfg: AIConfig):
        self.cfg = cfg
        self.cache = JSONCache(cfg.cache_dir)

    # 子类实现：真正的网络调用
    def _chat(self, system: str, user: str) -> str:  # pragma: no cover - 抽象
        raise NotImplementedError

    # ── 接入点① 情绪 ──
    def analyze_sentiment(self, texts):
        if not self.cfg.feature_on("sentiment") or not texts:
            return [SentimentResult(0.0, 0.0) for _ in texts]
        k = self.cache.key("sentiment", self.cfg.model, texts)
        cached = self.cache.get(k)
        if cached is None:
            try:
                raw = self._chat(_SENTIMENT_SYS, json.dumps(texts, ensure_ascii=False))
                cached = json.loads(_extract_json(raw))
                self.cache.set(k, cached)
            except Exception as e:  # noqa: BLE001
                log.warning("情绪分析失败，降级中性: %s", e)
                return [SentimentResult(0.0, 0.0) for _ in texts]
        out = []
        for item in cached:
            out.append(SentimentResult(
                sentiment=_clamp(item.get("sentiment", 0.0), -1, 1),
                confidence=_clamp(item.get("confidence", 0.0), 0, 1)))
        # 数量对齐
        while len(out) < len(texts):
            out.append(SentimentResult(0.0, 0.0))
        return out[:len(texts)]

    # ── 接入点① 事件 ──
    def extract_events(self, texts):
        if not self.cfg.feature_on("event_extraction") or not texts:
            return []
        k = self.cache.key("events", self.cfg.model, texts)
        cached = self.cache.get(k)
        if cached is None:
            try:
                raw = self._chat(_EVENT_SYS, json.dumps(texts, ensure_ascii=False))
                cached = json.loads(_extract_json(raw))
                self.cache.set(k, cached)
            except Exception as e:  # noqa: BLE001
                log.warning("事件抽取失败，降级空: %s", e)
                return []
        out = []
        for item in cached:
            et = str(item.get("event_type", "")).strip()
            if not et:
                continue
            out.append(EventResult(
                ticker=str(item.get("ticker", "")),
                event_type=et,
                confidence=_clamp(item.get("confidence", 0.0), 0, 1)))
        return out

    # ── 接入点② NL→筛选（绝不执行，且字段受 schema 约束）──
    def nl_to_filter(self, instruction, field_schema):
        if not self.cfg.feature_on("nl_to_filter"):
            return FilterSpecDTO(name="ai-disabled", conditions=[])
        user = json.dumps(
            {"instruction": instruction, "allowed_fields": list(field_schema)},
            ensure_ascii=False)
        try:
            raw = self._chat(_NL_FILTER_SYS, user)
            d = json.loads(_extract_json(raw))
        except Exception as e:  # noqa: BLE001
            log.warning("nl_to_filter 失败，降级空条件: %s", e)
            return FilterSpecDTO(name="ai-error", conditions=[])
        # 防注入：剔除不在白名单 schema 的字段与非法算子
        allowed_ops = {"<", "<=", ">", ">=", "==", "!="}
        conds = []
        for c in d.get("conditions", []):
            if c.get("field") in field_schema and c.get("op") in allowed_ops:
                conds.append({"field": c["field"], "op": c["op"], "value": c["value"]})
        return FilterSpecDTO(
            name=str(d.get("name", "nl-filter")),
            conditions=conds,
            logic="or" if d.get("logic") == "or" else "and",
            sort_by=d.get("sort_by") if d.get("sort_by") in field_schema else None,
            ascending=bool(d.get("ascending", False)),
            limit=d.get("limit") if isinstance(d.get("limit"), int) else None,
        )

    # ── 接入点③ 报告（RAG）──
    def generate_report(self, candidate_data):
        if not self.cfg.feature_on("report_generation"):
            return ReportResult("（report_generation 已关闭）", grounded=True)
        user = (
            "以下为系统提供的真实数据（JSON），请仅据此撰写分析，不得新增数字：\n"
            + json.dumps(candidate_data, ensure_ascii=False)
        )
        try:
            text = self._chat(_REPORT_SYS, user)
            return ReportResult(text=text.strip(), grounded=True)
        except Exception as e:  # noqa: BLE001
            log.warning("报告生成失败: %s", e)
            return ReportResult("（AI 报告生成失败，已降级。）", grounded=True)


def _clamp(v, lo, hi) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _extract_json(raw: str) -> str:
    """从模型回复中抽取 JSON 主体（容忍 ```json 包裹或前后赘述）。"""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    # 取首个 [ 或 { 到对应末尾
    for op, cl in (("[", "]"), ("{", "}")):
        i, j = s.find(op), s.rfind(cl)
        if i != -1 and j != -1 and j > i:
            return s[i:j + 1]
    return s
