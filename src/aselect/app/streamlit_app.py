"""Streamlit 界面（能用 Streamlit 不写前端）。

  streamlit run src/aselect/app/streamlit_app.py

展示：数据状态、多因子打分 + 条件筛选、K 线图、单只回测、AI 报告（禁用时降级占位）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许 `streamlit run path/to/streamlit_app.py` 直接找到包
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from aselect.ai import get_analyzer
from aselect.config import load_config
from aselect.data import build_cross_section
from aselect.engine import add_indicators, score_factors, screen
from aselect.engine.backtest import run_ma_backtest
from aselect.engine.indicators import backend as ind_backend
from aselect.engine.screener import Condition, FilterSpec
from aselect.field_schema import FIELD_SCHEMA
from aselect.storage import get_storage

st.set_page_config(page_title="个人选股 App", layout="wide")
cfg = load_config()


@st.cache_resource
def _store():
    return get_storage(cfg)


store = _store()

# ── 顶部：状态条 ──
st.title("📈 个人选股 App · A 股")
c1, c2, c3 = st.columns(3)
n_sym = len(store.get_symbols())
c1.metric("股票池", f"{n_sym} 只")
c2.metric("指标后端", ind_backend())
ai_state = "启用" if cfg.ai.enabled and cfg.ai.provider != "none" else "禁用(降级)"
c3.metric("AI", f"{ai_state} · {cfg.ai.provider}")

if n_sym == 0:
    st.warning("本地库为空。请先运行：`python -m aselect.cli seed`（离线合成数据）"
               "或 `python -m aselect.cli update`（akshare 真实数据）。")
    st.stop()

# ── 截面 + 因子打分 ──
cross = build_cross_section(store, cfg)
scored = score_factors(cross)

st.sidebar.header("筛选条件")
pe_max = st.sidebar.slider("PE 上限", 0, 100, 30)
roe_min = st.sidebar.slider("ROE 下限(%)", -10, 40, 10)
top_n = st.sidebar.number_input("取前 N", 5, 100, 20)

spec = FilterSpec(
    name="界面筛选",
    conditions=[Condition("pe", "<", pe_max), Condition("roe", ">", roe_min)],
    sort_by="total_score", ascending=False, limit=int(top_n),
)
result = screen(scored, spec)

st.subheader(f"候选股（命中 {len(result)} 只）")
show_cols = [c for c in ["symbol", "name", "pe", "pb", "roe", "revenue_yoy",
                         "mom_60", "score_value", "score_quality", "total_score"]
             if c in result.columns]
st.dataframe(result[show_cols], use_container_width=True)

# ── 单只详情：K 线 + 回测 + AI 报告 ──
st.divider()
syms = result["symbol"].tolist() or scored["symbol"].tolist()
pick = st.selectbox("查看个股", syms)
adjust = cfg.datasource.get("adjust", "hfq")
daily = store.get_daily(pick, adjust)

if not daily.empty:
    ind = add_indicators(daily)
    left, right = st.columns([2, 1])
    with left:
        st.markdown("**K 线 / 均线**")
        chart_df = ind.set_index("date")[["close", "ma20", "ma60"]]
        st.line_chart(chart_df)
    with right:
        st.markdown("**单只回测（MA 5/20 交叉，含 A 股交易摩擦）**")
        d = daily.copy()
        d["date"] = d["date"].dt.strftime("%Y-%m-%d")
        res = run_ma_backtest(d, cfg.backtest)
        st.write(f"引擎: `{res.engine}`")
        st.metric("总收益", f"{res.total_return:.2%}")
        st.metric("年化", f"{res.annual_return:.2%}")
        st.metric("夏普", res.sharpe)
        st.metric("最大回撤", f"{res.max_drawdown:.2%}")

    # ── AI 报告（RAG）：禁用时 NullAnalyzer 返回占位，不报错 ──
    st.markdown("**AI 分析报告**")
    if st.button("生成 AI 报告"):
        analyzer = get_analyzer(cfg)
        row = result[result["symbol"] == pick]
        payload = row[show_cols].to_dict("records")[0] if not row.empty else {"symbol": pick}
        report = analyzer.generate_report({"candidate": payload, "fields": FIELD_SCHEMA})
        st.info(report.text)

st.divider()
st.caption(cfg.disclaimer)
