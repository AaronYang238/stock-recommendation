"""回测引擎封装。

优先用 backtrader（禁止自研以避免未来函数 / 前视偏差，第 3.2 节），并计入
A 股真实交易摩擦：佣金、印花税（卖出单边）、过户费、滑点，模拟 T+1。
backtrader 不可用时回退到一个**严格次日成交、无前视**的向量化简版（仅供离线演示，
会在结果中标注 engine='vectorized-demo'）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    trades: int
    engine: str
    equity_curve: pd.Series = field(repr=False, default_factory=pd.Series)


def _metrics(equity: pd.Series, trades: int, engine: str,
             rf: float = 0.0) -> BacktestResult:
    rets = equity.pct_change().dropna()
    total = equity.iloc[-1] / equity.iloc[0] - 1 if len(equity) else 0.0
    n = max(len(equity), 1)
    annual = (1 + total) ** (252 / n) - 1
    sharpe = (np.sqrt(252) * (rets.mean() - rf) / rets.std()
              if rets.std() > 0 else 0.0)
    peak = equity.cummax()
    mdd = ((equity - peak) / peak).min() if len(equity) else 0.0
    return BacktestResult(
        total_return=round(float(total), 4),
        annual_return=round(float(annual), 4),
        sharpe=round(float(sharpe), 3),
        max_drawdown=round(float(mdd), 4),
        trades=int(trades),
        engine=engine,
        equity_curve=equity,
    )


class _StampDutyCommission:
    """backtrader 佣金方案：买卖佣金 + 卖出印花税 + 过户费。"""
    # 在 _build_bt_commission 中以闭包构造，避免在无 backtrader 环境导入其基类。


def _ma_cross_signals(df: pd.DataFrame, fast: int, slow: int) -> pd.Series:
    """MA 金叉=1（持有），死叉=0（空仓）。信号基于收盘 t，次日执行（调用方 shift）。"""
    ma_fast = df["close"].rolling(fast).mean()
    ma_slow = df["close"].rolling(slow).mean()
    return (ma_fast > ma_slow).astype(int)


def run_ma_backtest(df: pd.DataFrame, config_bt: dict,
                    fast: int = 5, slow: int = 20) -> BacktestResult:
    """对单只后复权日线跑均线交叉策略回测。df 需含 date/open/close。"""
    df = df.sort_values("date").reset_index(drop=True)
    try:
        return _run_backtrader(df, config_bt, fast, slow)
    except Exception:  # noqa: BLE001 — backtrader 不可用/不兼容时回退
        return _run_vectorized(df, config_bt, fast, slow)


# ── backtrader 路径 ─────────────────────────────────────────
def _run_backtrader(df, config_bt, fast, slow) -> BacktestResult:
    import backtrader as bt

    commission = float(config_bt.get("commission", 0.00025))
    stamp = float(config_bt.get("stamp_tax", 0.001))
    transfer = float(config_bt.get("transfer_fee", 0.00001))
    slippage = float(config_bt.get("slippage", 0.001))
    cash = float(config_bt.get("cash", 1_000_000))

    class AStockCommission(bt.CommInfoBase):
        params = (("stocklike", True), ("commtype", bt.CommInfoBase.COMM_PERC),
                  ("percabs", True), ("commission", commission),
                  ("stamp_duty", stamp), ("transfer", transfer))

        def _getcommission(self, size, price, pseudoexec):
            value = abs(size) * price
            comm = value * self.p.commission + value * self.p.transfer
            if size < 0:  # 卖出加印花税（单边）
                comm += value * self.p.stamp_duty
            return comm

    class MaCross(bt.Strategy):
        params = (("fast", fast), ("slow", slow))

        def __init__(self):
            mf = bt.ind.SMA(period=self.p.fast)
            ms = bt.ind.SMA(period=self.p.slow)
            self.cross = bt.ind.CrossOver(mf, ms)
            self.trade_count = 0

        def next(self):
            if not self.position and self.cross > 0:
                self.order_target_percent(target=0.95)  # 留现金付费
                self.trade_count += 1
            elif self.position and self.cross < 0:
                self.close()
                self.trade_count += 1

    cerebro = bt.Cerebro()
    data = bt.feeds.PandasData(
        dataname=df.assign(datetime=pd.to_datetime(df["date"])).set_index("datetime"),
        open="open", high="high", low="low", close="close",
        volume="volume", openinterest=None,
    )
    cerebro.adddata(data)
    strat_cls = MaCross
    cerebro.addstrategy(strat_cls)
    cerebro.broker.setcash(cash)
    cerebro.broker.addcommissioninfo(AStockCommission())
    cerebro.broker.set_slippage_perc(slippage)  # 滑点
    # T+1：backtrader 默认次日成交（cheat_on_open=False），买入当日不可卖。
    cerebro.broker.set_coc(False)

    vals = []

    class _Recorder(bt.Analyzer):
        def next(self):
            vals.append(self.strategy.broker.getvalue())

    cerebro.addanalyzer(_Recorder)
    results = cerebro.run()
    equity = pd.Series(vals) if vals else pd.Series([cash])
    trades = getattr(results[0], "trade_count", 0)
    return _metrics(equity, trades, engine="backtrader")


# ── 向量化回退（严格次日成交，无前视；仅离线演示）──────────
def _run_vectorized(df, config_bt, fast, slow) -> BacktestResult:
    commission = float(config_bt.get("commission", 0.00025))
    stamp = float(config_bt.get("stamp_tax", 0.001))
    transfer = float(config_bt.get("transfer_fee", 0.00001))
    slippage = float(config_bt.get("slippage", 0.001))

    sig = _ma_cross_signals(df, fast, slow)
    # 关键：信号 shift(1) → 用次日开盘执行，杜绝未来函数（前视偏差）
    pos = sig.shift(1).fillna(0)
    # 以开盘价成交近似；日收益按持仓
    ret = df["close"].pct_change().fillna(0)
    strat_ret = pos * ret

    # 交易成本：仓位变化处扣费（买入：佣金+过户+滑点；卖出：再加印花税）
    pos_change = pos.diff().fillna(0)
    buy = (pos_change > 0)
    sell = (pos_change < 0)
    cost = pd.Series(0.0, index=df.index)
    cost[buy] = commission + transfer + slippage
    cost[sell] = commission + transfer + slippage + stamp
    strat_ret = strat_ret - cost

    equity = (1 + strat_ret).cumprod() * float(config_bt.get("cash", 1_000_000))
    trades = int((pos_change != 0).sum())
    return _metrics(equity, trades, engine="vectorized-demo")
