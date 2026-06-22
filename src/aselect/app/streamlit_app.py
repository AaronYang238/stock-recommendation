"""Streamlit 界面（能用 Streamlit 不写前端）。

  streamlit run src/aselect/app/streamlit_app.py   # 默认端口 9090

展示：数据状态、多因子打分 + 条件筛选（带板块标注）、K 线图、单只回测、AI 报告。
界面上的专有名词带 ⓘ 标识，鼠标悬停即显示通俗解释（术语词典见 aselect.glossary）。
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
from aselect.glossary import describe
from aselect.storage import get_storage

st.set_page_config(page_title="个人选股 App", layout="wide")
cfg = load_config()


# ── 术语悬浮提示：在名词右上角加 ⓘ，hover 显示解释 ─────────────
def term(label: str, key: str | None = None) -> str:
    """返回带 ⓘ 悬浮提示的 HTML 片段（配合 st.markdown(unsafe_allow_html=True)）。"""
    desc = describe(key or label)
    if not desc:
        return label
    safe = desc.replace('"', "&quot;")
    return (
        f'{label}'
        f'<sup title="{safe}" '
        f'style="cursor:help;color:#2e9b8f;font-weight:bold;font-size:0.8em;">&#9432;</sup>'
    )


def header(label: str, *terms: str) -> None:
    """渲染小节标题，并在其后追加若干带 ⓘ 的术语提示。"""
    chips = " ".join(term(t) for t in terms)
    st.markdown(f"**{label}** &nbsp; {chips}", unsafe_allow_html=True)


# 候选表：列 → (中文表头, 术语词典键)
_COLS = {
    "symbol": ("代码", None), "name": ("名称", None), "board": ("板块", "板块"),
    "status_label": ("状态", "状态"),
    "pe": ("PE", "PE"), "pb": ("PB", "PB"), "roe": ("ROE", "ROE"),
    "revenue_yoy": ("营收同比", "营收同比"), "mom_60": ("动量(60)", "动量"),
    "score_value": ("价值分", "多因子综合得分"), "score_quality": ("质量分", "多因子综合得分"),
    "total_score": ("综合得分", "多因子综合得分"),
}
_BOARD_HELP = ("所属板块：主板(沪60x/深00x)、创业板(300/301)、科创板(688/689)、"
               "北交所(4/8/920)。涨跌幅与开通门槛不同。")


def _column_config(cols: list[str]) -> dict:
    out = {}
    for c in cols:
        label, key = _COLS.get(c, (c, None))
        help_ = _BOARD_HELP if c == "board" else (describe(key) if key else None)
        out[c] = st.column_config.Column(label, help=help_)
    return out


@st.cache_resource
def _store():
    return get_storage(cfg)


store = _store()

# ── 顶部：状态条 ──
st.title("📈 个人选股 App · A 股")
c1, c2, c3 = st.columns(3)
n_sym = len(store.get_symbols())
c1.metric("股票池", f"{n_sym} 只")
c2.metric("指标后端", ind_backend(),
          help="技术指标计算库。pandas-ta 不可用时自动回退到经测试的向量化实现。")
ai_state = "启用" if cfg.ai.enabled and cfg.ai.provider != "none" else "禁用(降级)"
c3.metric("AI", f"{ai_state} · {cfg.ai.provider}",
          help="AI 为热插拔模块。禁用或缺 Key 时降级为 NullAnalyzer，确定性核心照常运行。")

if n_sym == 0:
    st.warning("本地库为空。请先运行：`python -m aselect.cli seed`（离线合成数据）"
               "或 `python -m aselect.cli update`（akshare 真实数据）。")
    st.stop()

# ── 截面 + 因子打分 ──
cross = build_cross_section(store, cfg)
scored = score_factors(cross)

st.sidebar.header("筛选条件")
pe_max = st.sidebar.slider("PE 上限", 0, 100, 30, help=describe("PE"))
roe_min = st.sidebar.slider("ROE 下限(%)", -10, 40, 10, help=describe("ROE"))
top_n = st.sidebar.number_input("取前 N", 5, 100, 20)
boards = sorted(scored["board"].dropna().unique()) if "board" in scored else []
pick_boards = st.sidebar.multiselect("板块", boards, default=boards,
                                     help=_BOARD_HELP)
statuses = sorted(scored["status_label"].dropna().unique()) if "status_label" in scored else []
# 默认排除「退市」，避免误把已退市标的当候选；用户可手动勾选纳入
default_status = [s for s in statuses if s != "退市"]
pick_status = st.sidebar.multiselect("状态（ST/退市）", statuses, default=default_status,
                                     help=describe("状态"))

spec = FilterSpec(
    name="界面筛选",
    conditions=[Condition("pe", "<", pe_max), Condition("roe", ">", roe_min)],
    sort_by="total_score", ascending=False, limit=int(top_n),
)
result = screen(scored, spec)
if pick_boards and "board" in result.columns:
    result = result[result["board"].isin(pick_boards)]
if pick_status and "status_label" in result.columns:
    result = result[result["status_label"].isin(pick_status)]

header(f"候选股（命中 {len(result)} 只）", "多因子综合得分")
show_cols = [c for c in ["symbol", "name", "board", "status_label", "pe", "pb",
                         "roe", "revenue_yoy", "mom_60", "score_value",
                         "score_quality", "total_score"]
             if c in result.columns]
st.dataframe(result[show_cols], use_container_width=True,
             column_config=_column_config(show_cols), hide_index=True)
st.caption("分类维度：板块(主板/创业板/科创板/北交所) + 状态(正常/ST/退市) "
           "—— 鼠标悬停表头 ⓘ 查看说明。默认已排除退市标的。")

# ── 单只详情：K 线 + 回测 + AI 报告 ──
st.divider()
syms = result["symbol"].tolist() or scored["symbol"].tolist()
pick = st.selectbox("查看个股", syms)
if "board" in scored.columns and pick:
    brow = scored.loc[scored["symbol"] == pick]
    if not brow.empty:
        bd = brow["board"].iloc[0]
        sl = brow["status_label"].iloc[0] if "status_label" in brow else ""
        chips = term(f"板块：{bd}", "板块") + " &nbsp; " + term(f"状态：{sl}", "状态")
        st.markdown(chips, unsafe_allow_html=True)
        if sl in ("ST", "退市"):
            st.warning(f"⚠️ 该标的为「{sl}」，风险较高，请谨慎。")
adjust = cfg.datasource.get("adjust", "hfq")
daily = store.get_daily(pick, adjust)

if not daily.empty:
    ind = add_indicators(daily)
    left, right = st.columns([2, 1])
    with left:
        header("K 线 / 均线", "MA", "后复权")
        chart_df = ind.set_index("date")[["close", "ma20", "ma60"]]
        st.line_chart(chart_df)
    with right:
        header("单只回测（MA 5/20 交叉）", "交易摩擦", "T+1")
        d = daily.copy()
        d["date"] = d["date"].dt.strftime("%Y-%m-%d")
        res = run_ma_backtest(d, cfg.backtest)
        st.write(f"引擎: `{res.engine}`")
        st.metric("总收益", f"{res.total_return:.2%}",
                  help=describe("年化收益"))
        st.metric("年化", f"{res.annual_return:.2%}", help=describe("年化收益"))
        st.metric("夏普", res.sharpe, help=describe("夏普比率"))
        st.metric("最大回撤", f"{res.max_drawdown:.2%}", help=describe("最大回撤"))

    # ── AI 报告（RAG）：禁用时 NullAnalyzer 返回占位，不报错 ──
    header("AI 分析报告", "RAG")
    if st.button("生成 AI 报告"):
        analyzer = get_analyzer(cfg)
        row = result[result["symbol"] == pick]
        payload = row[show_cols].to_dict("records")[0] if not row.empty else {"symbol": pick}
        report = analyzer.generate_report({"candidate": payload, "fields": FIELD_SCHEMA})
        st.info(report.text)

st.divider()
st.caption(cfg.disclaimer)
