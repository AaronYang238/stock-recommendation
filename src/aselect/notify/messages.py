"""三类通知消息的纯函数格式化（候选 / 离场提醒 / 风险预警）。无 I/O、确定性。"""
from __future__ import annotations


def format_candidates(rows: list[dict], date: str) -> tuple[str, list[str]]:
    """每日候选：打分排序 + 入场闸门状态。"""
    title = f"【选股候选】{date}"
    lines = []
    for i, r in enumerate(rows, 1):
        gate = "✅过闸门" if r.get("gate_passed") else "⛔闸门未过"
        lines.append(
            f"{i}. {r.get('symbol')} {r.get('name', '')}"
            f"（{r.get('industry', '-')}）分{float(r.get('total_score', 0)):.2f} · {gate}")
    if not lines:
        lines = ["（无候选）"]
    return title, lines


def format_exit_alert(trade: dict) -> tuple[str, list[str]]:
    """持仓离场提醒（移动止损/趋势破位等触发）。"""
    title = "【离场提醒】"
    ret = float(trade.get("ret", 0)) * 100
    lines = [
        f"{trade.get('symbol')} {trade.get('name', '')} 触发离场：{trade.get('reason')}",
        f"收益 {ret:+.2f}% · 建仓 {trade.get('entry_date')} → 离场 {trade.get('exit_date')}",
    ]
    return title, lines


def format_risk_warning(warnings: list[str]) -> tuple[str, list[str]]:
    """风险预警（集中度 / 追高等）：逐条列出。"""
    title = "【风险预警】"
    lines = [f"⚠️ {w}" for w in warnings] or ["（无预警）"]
    return title, lines
