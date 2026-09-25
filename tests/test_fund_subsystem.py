"""基金子系统测试：落库幂等、信号确定性、费率阶梯、回测手工算例。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aselect.fund.backtest import FundFeeModel, run_fund_backtest
from aselect.fund.strategy import (composite_score, cross_section_factors,
                                   fund_factor_row, icir_shrink_weights,
                                   screen_universe, zscore)
from aselect.storage.sqlite_store import SQLiteStorage


@pytest.fixture()
def store(tmp_path):
    s = SQLiteStorage(str(tmp_path / "t.sqlite"))
    yield s
    s.close()


def _nav_index(n=300, start="2020-01-01"):
    return pd.bdate_range(start, periods=n)


# ── 净值落库幂等 ──
def test_fund_nav_upsert_idempotent(store):
    idx = _nav_index(10)
    df = pd.DataFrame({"fund_code": "000001", "nav_date": idx.strftime("%Y-%m-%d"),
                       "nav": np.linspace(1.0, 1.1, 10)})
    store.upsert_fund_nav(df)
    store.upsert_fund_nav(df)          # 重复写入
    got = store.get_fund_nav("000001")
    assert len(got) == 10              # 不重复
    # 覆盖更新
    df2 = df.copy()
    df2.loc[0, "nav"] = 9.99
    store.upsert_fund_nav(df2)
    got = store.get_fund_nav("000001")
    assert float(got.iloc[0]["nav"]) == 9.99
    assert store.fund_codes_with_nav() == ["000001"]


def test_fund_nav_wide_alignment(store):
    i1 = _nav_index(5)
    i2 = _nav_index(8)
    store.upsert_fund_nav(pd.DataFrame({
        "fund_code": "A", "nav_date": i1.strftime("%Y-%m-%d"),
        "nav": np.linspace(1, 2, 5)}))
    store.upsert_fund_nav(pd.DataFrame({
        "fund_code": "B", "nav_date": i2.strftime("%Y-%m-%d"),
        "nav": np.linspace(2, 1, 8)}))
    wide = store.get_fund_nav_wide()
    assert wide.shape == (8, 2)
    assert pd.isna(wide["A"].iloc[-1])          # 不前向填充：缺失就是缺失
    assert wide["A"].iloc[0] == pytest.approx(1.0)


def test_fund_info_upsert(store):
    df = pd.DataFrame([{"fund_code": "000001", "name": "测试混合", "kind": "open",
                        "fund_type": "混合型-偏股", "scale_yi": 10.0,
                        "setup_date": "2015-01-01", "ret_1y": 0.2}])
    store.upsert_fund_info(df)
    got = store.get_fund_info(kind="open")
    assert len(got) == 1 and got.iloc[0]["name"] == "测试混合"


# ── 初筛 ──
def test_screen_universe_filters():
    info = pd.DataFrame([
        {"fund_code": "1", "kind": "open", "fund_type": "混合型-偏股",
         "scale_yi": 10.0, "setup_date": "2010-01-01", "ret_1y": 0.3},
        {"fund_code": "2", "kind": "open", "fund_type": "混合型-偏股",
         "scale_yi": 1.0, "setup_date": "2010-01-01", "ret_1y": 0.3},   # 规模小
        {"fund_code": "3", "kind": "open", "fund_type": "债券型",
         "scale_yi": 10.0, "setup_date": "2010-01-01", "ret_1y": 0.3},  # 类型
        {"fund_code": "4", "kind": "open", "fund_type": "股票型",
         "scale_yi": 10.0, "setup_date": "2026-01-01", "ret_1y": 0.3},  # 太新
        {"fund_code": "5", "kind": "open", "fund_type": "股票型",
         "scale_yi": 10.0, "setup_date": "2010-01-01", "ret_1y": -0.1},  # 近1年负
        {"fund_code": "etf:510300", "kind": "etf", "fund_type": "ETF",
         "scale_yi": 100.0, "setup_date": "2012-01-01", "ret_1y": None},
    ])
    out = screen_universe(info, min_scale_yi=2.0, min_age_days=365,
                          min_ret_1y=0.0, today="2026-09-25")
    # ETF 保留（豁免类型过滤），但 ret_1y 缺失 → 业绩门槛不过
    assert set(out["fund_code"]) == {"1"}


# ── 信号合成确定性 ──
def _mk_wide(seed=7, n=400):
    idx = _nav_index(n)
    rng = np.random.default_rng(seed)
    w = pd.DataFrame({
        "A": np.cumprod(1 + rng.normal(0.0012, 0.01, n)),   # 强动量
        "B": np.cumprod(1 + rng.normal(0.0006, 0.008, n)),
        "C": np.cumprod(1 + rng.normal(-0.0003, 0.012, n)),  # 弱
    }, index=idx)
    return w


def test_fund_factor_row_deterministic():
    w = _mk_wide()
    r1 = fund_factor_row(w["A"], w.index[-1], 10.0, 0.0015, 10.0, 0.0015)
    r2 = fund_factor_row(w["A"], w.index[-1], 10.0, 0.0015, 10.0, 0.0015)
    assert r1 == r2
    assert r1["mom3"] > 0
    assert r1["dd_pen"] <= 1.0        # -dd ≤ 1
    assert abs(r1["size_fee"]) < 1e-9  # 等于中位 → 中性
    assert fund_factor_row(w["A"].iloc[:30], w.index[-1], 10, 0.0015, 10, 0.0015) is None


def test_cross_section_and_composite_deterministic():
    w = _mk_wide()
    info = pd.DataFrame({"fund_code": ["A", "B", "C"],
                         "scale_yi": [10.0, 20.0, 5.0],
                         "fee_rate": [0.0015] * 3}).set_index("fund_code")
    f1 = cross_section_factors(w, w.index[-1], info)
    f2 = cross_section_factors(w, w.index[-1], info)
    pd.testing.assert_frame_equal(f1, f2)               # 确定性
    wts = {"mom3": 0.4, "mom6": 0.3, "dd_pen": 0.2, "size_fee": 0.1}
    s1 = composite_score(f1, wts)
    s2 = composite_score(f1, wts)
    pd.testing.assert_series_equal(s1, s2)
    assert s1.index[0] == "A"          # 强动量排第一
    # 无前视：只用截至 T 的数据 → T 之后的净值变化不影响 T 的因子
    w2 = w.copy()
    w2.iloc[-1] *= 1.5
    f3 = cross_section_factors(w2, w.index[-2], info)
    f4 = cross_section_factors(w, w.index[-2], info)
    pd.testing.assert_frame_equal(f3, f4)


def test_zscore_neutral_on_constant():
    s = pd.Series([2.0, 2.0, 2.0], index=list("abc"))
    assert (zscore(s) == 0).all()


def test_icir_shrink_weights_caps_and_neutral():
    ic = pd.DataFrame({
        "mom3": [0.05, 0.06, 0.04, 0.05],   # 稳定正
        "mom6": [0.10, -0.09, 0.11, -0.10],  # 均值≈0（ICIR≈0.043，小幅正）
        "dd_pen": [0.01, 0.02, 0.01, 0.02],
    })
    w = icir_shrink_weights(ic, shrink=0.5, max_weight=0.4)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(v <= 0.4 + 1e-9 for v in w.values())
    assert w["mom3"] > w["dd_pen"] > 0
    assert w["mom6"] >= 0.0 and w["mom6"] < w["dd_pen"]
    # 纯噪声（均值恰 0、另一因子 std=0）→ 全部非正 → 等权兜底（与 factor_weights 约定一致）
    zero = pd.DataFrame({
        "f1": [0.10, -0.10, 0.10, -0.10],
        "f2": [0.02, 0.02, 0.02, 0.02],
    })
    w2 = icir_shrink_weights(zero, shrink=0.5, max_weight=0.4)
    assert w2["f1"] == pytest.approx(0.5)
    assert w2["f2"] == pytest.approx(0.5)
    # 全负 IC → 等权
    neg = ic * -1
    w3 = icir_shrink_weights(neg, shrink=0.5, max_weight=0.4)
    assert abs(w3["mom3"] - 1 / 3) < 1e-9


# ── 费率阶梯 ──
def test_redeem_fee_ladder():
    f = FundFeeModel()
    assert f.redeem_rate(0) == 0.015
    assert f.redeem_rate(6) == 0.015
    assert f.redeem_rate(7) == 0.0075
    assert f.redeem_rate(29) == 0.0075
    assert f.redeem_rate(30) == 0.005
    assert f.redeem_rate(364) == 0.005
    assert f.redeem_rate(365) == 0.0


# ── 回测手工算例 ──
def test_backtest_manual_subscription_fee():
    """单基金、净值线性上升、只买不卖：最终净值 = 净申购份额 × 末日净值。"""
    idx = pd.bdate_range("2021-01-04", "2021-03-31")
    w = pd.DataFrame({"A": np.linspace(1.0, 1.2, len(idx))}, index=idx)
    res = run_fund_backtest(w, pd.Series(dtype=float),
                            {idx[0]: pd.Series({"A": 1.0})},
                            top_n=1, cash=100_000.0, confirm_lag=1)
    net = 100_000 / 1.0015                 # 外扣法
    expected = (net / w["A"].loc[idx[1]]) * w["A"].iloc[-1]
    assert abs(res.equity.iloc[-1] - expected) < 1e-6
    assert res.turnover_costs == pytest.approx(100_000 - net, abs=1e-6)


def test_backtest_redeem_ladder_short_and_long():
    """买后立即清仓 → <7 天 1.5%；持有一年半清仓 → 0。"""
    net_rate = 1 / 1.0015
    i1 = pd.bdate_range("2021-01-04", "2021-03-31")
    w1 = pd.DataFrame({"A": 1.0}, index=i1)
    reb = {i1[0]: pd.Series({"A": 1.0}),
           i1[2]: pd.Series(dtype=float, index=["A"])}     # 确认 i1[1] 买, i1[3] 卖
    r1 = run_fund_backtest(w1, pd.Series(dtype=float), reb, top_n=1, cash=100_000.0)
    assert r1.equity.iloc[-1] == pytest.approx(100_000 * net_rate * 0.985, abs=0.01)

    i2 = pd.bdate_range("2021-01-04", "2022-08-31")
    w2 = pd.DataFrame({"A": 1.0}, index=i2)
    reb2 = {i2[0]: pd.Series({"A": 1.0}),
            i2[-2]: pd.Series(dtype=float, index=["A"])}
    r2 = run_fund_backtest(w2, pd.Series(dtype=float), reb2, top_n=1, cash=100_000.0)
    assert r2.equity.iloc[-1] == pytest.approx(100_000 * net_rate, abs=0.01)


def test_backtest_no_lookahead_in_scores():
    """把 T 日之后的净值全部改掉，T 日出信号的回测结果必须不变。"""
    idx = pd.bdate_range("2021-01-04", "2021-12-31")
    rng = np.random.default_rng(3)
    base = pd.DataFrame({
        "A": np.cumprod(1 + rng.normal(0.001, 0.01, len(idx))),
        "B": np.cumprod(1 + rng.normal(0.0005, 0.008, len(idx))),
        "C": np.cumprod(1 + rng.normal(0.0002, 0.009, len(idx))),
    }, index=idx)
    # 单次调仓：idx[100] 出信号
    reb = {idx[100]: pd.Series({"A": 2.0, "B": 1.0, "C": 0.5})}
    r1 = run_fund_backtest(base, pd.Series(dtype=float), reb, top_n=2, cash=100_000.0)
    tampered = base.copy()
    tampered.iloc[101:] *= 1.3            # 信号日之后全改
    r2 = run_fund_backtest(tampered, pd.Series(dtype=float), reb, top_n=2, cash=100_000.0)
    # 信号日之前的曲线必须完全一致（成交在 T+1，用的是 T+1 净值，前 101 天同）
    pd.testing.assert_series_equal(
        r1.equity.iloc[:101], r2.equity.iloc[:101])


def test_backtest_monthly_rebalance_topn():
    """月频 TopN：持仓历史落在每月末后一天（T+1 确认），每月换仓成本>0。"""
    idx = pd.bdate_range("2021-01-04", "2021-12-31")
    rng = np.random.default_rng(11)
    # A 一直强，B/C 交替 → 验证 TopN 选 A
    n = len(idx)
    w = pd.DataFrame({
        "A": np.cumprod(1 + rng.normal(0.002, 0.005, n)),
        "B": np.cumprod(1 + rng.normal(0.0005, 0.01, n)),
        "C": np.cumprod(1 + rng.normal(0.0002, 0.01, n)),
    }, index=idx)
    mes = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).max()
    scores = {d: pd.Series({"A": 3.0, "B": 2.0, "C": 1.0}) for d in mes}
    res = run_fund_backtest(w, pd.Series(dtype=float), scores, top_n=2)
    # 最后一个月末信号 T+1 确认，可能落在样本外 → 允许少一次
    assert res.n_rebalances in (len(mes) - 1, len(mes))
    assert res.n_rebalances >= 10
    for d, hold in res.holdings_history.items():
        assert hold == ["A", "B"]
    assert res.turnover_costs > 0
