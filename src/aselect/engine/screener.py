"""条件筛选器：受控 DSL（FilterSpec），可命名/保存/复用为「策略」。

这是 AI 接入点②(nl_to_filter) 的落地目标：AI 只能产出这种**参数化结构**，
绝不生成可执行代码；执行完全由本确定性引擎完成（铁律一 + 第 4.2 节防注入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# 允许的算子白名单（防注入：只认这些，不做 eval）
_OPS = {
    "<": lambda s, v: s < v,
    "<=": lambda s, v: s <= v,
    ">": lambda s, v: s > v,
    ">=": lambda s, v: s >= v,
    "==": lambda s, v: s == v,
    "!=": lambda s, v: s != v,
}


@dataclass(frozen=True)
class Condition:
    field: str          # 列名（受 field_schema 约束）
    op: str             # 必须 ∈ _OPS
    value: float | int | str

    def __post_init__(self):
        if self.op not in _OPS:
            raise ValueError(f"非法算子: {self.op}（允许: {list(_OPS)}）")


@dataclass(frozen=True)
class FilterSpec:
    """一组条件 + 逻辑关系。logic ∈ {and, or}。可序列化为 JSON 保存为策略。"""
    name: str = "unnamed"
    conditions: list[Condition] = field(default_factory=list)
    logic: str = "and"
    sort_by: str | None = None
    ascending: bool = False
    limit: int | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "logic": self.logic,
            "conditions": [c.__dict__ for c in self.conditions],
            "sort_by": self.sort_by, "ascending": self.ascending, "limit": self.limit,
        }

    @classmethod
    def from_dict(cls, d: dict, field_schema: dict | None = None) -> "FilterSpec":
        """从 JSON 还原；若给定 field_schema，则校验字段合法（防注入）。"""
        conds = []
        for c in d.get("conditions", []):
            fld = c["field"]
            if field_schema is not None and fld not in field_schema:
                raise ValueError(f"未知字段: {fld}")
            conds.append(Condition(fld, c["op"], c["value"]))
        return cls(
            name=d.get("name", "unnamed"),
            conditions=conds,
            logic=d.get("logic", "and"),
            sort_by=d.get("sort_by"),
            ascending=bool(d.get("ascending", False)),
            limit=d.get("limit"),
        )


def screen(df: pd.DataFrame, spec: FilterSpec) -> pd.DataFrame:
    """对截面 DataFrame 应用 FilterSpec，返回命中子集。"""
    if not spec.conditions:
        result = df
    else:
        masks = []
        for c in spec.conditions:
            if c.field not in df.columns:
                # 缺列视为不命中，避免 KeyError 中断
                masks.append(pd.Series(False, index=df.index))
                continue
            masks.append(_OPS[c.op](df[c.field], c.value))
        combined = masks[0]
        for m in masks[1:]:
            combined = (combined & m) if spec.logic == "and" else (combined | m)
        result = df[combined]

    if spec.sort_by and spec.sort_by in result.columns:
        result = result.sort_values(spec.sort_by, ascending=spec.ascending)
    if spec.limit:
        result = result.head(spec.limit)
    return result
