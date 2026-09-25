"""月频基金回测（宪法级：扣申赎费 + T+1 净值确认 + 赎回费持有期阶梯）。

口径：
  - 每月最后一个交易日（T）出信号（只用 ≤T 净值）→ T+1 日按净值确认申赎
    （场外基金 T+1，etf 用 T+1 净值近似 + 佣金/滑点）。
  - 申购费外扣法：净申购 = 金额/(1+费率)，费用 = 金额 - 净申购。
  - 赎回费按**实际持有天数**阶梯：<7 天 1.5%、<30 天 0.75%、<365 天 0.5%、
    ≥365 天 0（config 可调）。持有天数按申购确认日→赎回确认日的日历日。
  - 组合逐日按持有基金净值估值；再平衡到目标权重（TopN 等权）。
  - 基准：沪深300（index_daily 表或数据源现拉）。

确定性：全部数值计算纯 pandas，无 LLM、无网络（数据由调用方注入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class FundFeeModel:
    """申赎费率（小数）。场外外扣法；ETF 走佣金+滑点。"""
    subscribe: float = 0.0015
    redeem_lte7d: float = 0.015
    redeem_lte30d: float = 0.0075
    redeem_lte365d: float = 0.005
    redeem_gt365d: float = 0.0
    etf_commission: float = 0.00025
    etf_slippage: float = 0.0005

    @classmethod
    def from_config(cls, cfg: dict) -> "FundFeeModel":
        fee = (cfg or {}).get("fee", {}) or {}
        etf = (cfg or {}).get("etf", {}) or {}
        return cls(
            subscribe=float(fee.get("subscribe", 0.0015)),
            redeem_lte7d=float(fee.get("redeem_lte7d", 0.015)),
            redeem_lte30d=float(fee.get("redeem_lte30d", 0.0075)),
            redeem_lte365d=float(fee.get("redeem_lte365d", 0.005)),
            redeem_gt365d=float(fee.get("redeem_gt365d", 0.0)),
            etf_commission=float(etf.get("commission", 0.00025)),
            etf_slippage=float(etf.get("slippage", 0.0005)),
        )

    def redeem_rate(self, hold_days: int) -> float:
        """持有天数 → 赎回费率（阶梯，边界含入：7天按 <7 判）。"""
        if hold_days < 7:
            return self.redeem_lte7d
        if hold_days < 30:
            return self.redeem_lte30d
        if hold_days < 365:
            return self.redeem_lte365d
        return self.redeem_gt365d


@dataclass
class _Lot:
    """一笔申购（份额槽）：赎回费按槽算持有期。"""
    shares: float
    conf_date: pd.Timestamp      # 申购确认日
    kind: str                    # open / etf


@dataclass
class FundBacktestResult:
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    bench: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    holdings_history: dict = field(default_factory=dict)   # 调仓日 → fund list
    turnover_costs: float = 0.0
    n_rebalances: int = 0

    @property
    def equity_curve(self) -> pd.Series:
        """与 runner._oos_returns 的 OOS 审计口径兼容。"""
        return self.equity

    @property
    def period_returns(self) -> pd.Series:
        """调仓期收益（供 _oos_returns 优先取用）。"""
        eq = self.equity.dropna()
        if len(eq) < 2:
            return pd.Series(dtype=float)
        ret = eq.pct_change()
        # 按月聚合：近似调仓期收益（月频策略）
        return ret.groupby(ret.index.to_period("M")).apply(lambda x: (1 + x).prod() - 1)

    def summary(self) -> dict:
        eq = self.equity.dropna()
        ret = eq.pct_change().dropna()
        if len(eq) < 2:
            return {}
        years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
        ann = float(eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
        sd = ret.std() * np.sqrt(252)
        sharpe = float(ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0.0
        dd = float((eq / eq.cummax() - 1).min())
        calmar = ann / abs(dd) if dd < 0 else float("inf")
        out = {"ann_ret": ann, "sharpe": sharpe, "max_dd": dd, "calmar": calmar,
               "total_ret": float(eq.iloc[-1] / eq.iloc[0] - 1),
               "turnover_costs": self.turnover_costs,
               "n_rebalances": self.n_rebalances,
               "start": str(eq.index[0].date()), "end": str(eq.index[-1].date())}
        if not self.bench.dropna().empty:
            b = self.bench.dropna()
            b = b[(b.index >= eq.index[0]) & (b.index <= eq.index[-1])]
            if len(b) > 1:
                byears = max((b.index[-1] - b.index[0]).days / 365.25, 1e-9)
                bann = float(b.iloc[-1] / b.iloc[0]) ** (1 / byears) - 1
                br = b.pct_change().dropna()
                bdd = float((b / b.cummax() - 1).min())
                out["bench_ann_ret"] = bann
                out["bench_sharpe"] = (float(br.mean() / br.std() * np.sqrt(252))
                                       if br.std() > 0 else 0.0)
                out["bench_max_dd"] = bdd
                out["bench_calmar"] = bann / abs(bdd) if bdd < 0 else float("inf")
                out["bench_total_ret"] = float(b.iloc[-1] / b.iloc[0] - 1)
        # 分年
        yearly = {}
        for y, grp in ret.groupby(ret.index.year):
            yearly[int(y)] = float((1 + grp).prod() - 1)
        out["yearly"] = yearly
        if not self.bench.dropna().empty:
            br = self.bench.dropna().pct_change().dropna()
            br = br[(br.index >= eq.index[0]) & (br.index <= eq.index[-1])]
            out["bench_yearly"] = {int(y): float((1 + g).prod() - 1)
                                   for y, g in br.groupby(br.index.year)}
        # 月度胜率：仅展示（宪法禁作优化/验收目标）
        m = ret.groupby(ret.index.to_period("M")).apply(lambda x: (1 + x).prod() - 1)
        out["monthly_win_rate_display_only"] = float((m > 0).mean()) if len(m) else None
        return out


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """每个 (year, month) 的最后一个交易日。"""
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).max())


def run_fund_backtest(wide: pd.DataFrame, bench_close: pd.Series,
                      scores_by_reb: dict[pd.Timestamp, pd.Series],
                      fee: FundFeeModel | None = None,
                      top_n: int = 10, confirm_lag: int = 1,
                      cash: float = 1_000_000.0,
                      start: str | None = None, end: str | None = None,
                      ) -> FundBacktestResult:
    """月频 TopN 等权基金回测（事件驱动，见 _run）。

    wide        : index=nav_date, columns=fund_code, values=nav（全部候选）。
    bench_close : index=date, values=close（基准，可空）。
    scores_by_reb: 调仓日(T，月末最后交易日) → 截面总分（仅 ≤T 信息）。
    confirm_lag : 申赎确认延迟（交易日；场外=1）。
    """
    fee = fee or FundFeeModel()
    return _run(wide, bench_close, scores_by_reb, fee, top_n, confirm_lag,
                cash, start, end)


def _run(wide: pd.DataFrame, bench_close: pd.Series,
         scores_by_reb: dict[pd.Timestamp, pd.Series],
         fee: FundFeeModel, top_n: int, confirm_lag: int,
         cash: float, start: str | None, end: str | None) -> FundBacktestResult:
    wide = wide.sort_index()
    idx = pd.DatetimeIndex(wide.index)
    if start:
        idx = idx[idx >= pd.Timestamp(start)]
    if end:
        idx = idx[idx <= pd.Timestamp(end)]
    nav_at = {c: wide[c].dropna().to_dict() for c in wide.columns}

    def _nav_on(code: str, d: pd.Timestamp) -> float | None:
        nd = nav_at.get(code)
        if not nd:
            return None
        if d in nd:
            return nd[d]
        lo, hi = 0, len(nd) - 1
        keys = list(nd.keys())
        if keys[0] > d:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if keys[mid] <= d:
                lo = mid
            else:
                hi = mid - 1
        return nd[keys[lo]]

    reb_dates = [d for d in month_end_dates(idx) if d in scores_by_reb]
    pending: list[tuple[pd.Timestamp, list[str]]] = []   # (确认日, 目标基金)
    pos: dict[str, list[_Lot]] = {}
    cash_now = cash
    equity_curve: dict[pd.Timestamp, float] = {}
    costs_total = 0.0
    holdings_history: dict = {}
    n_reb = 0
    all_dates = list(idx)

    for di, today in enumerate(all_dates):
        # ── 1) 今日到期的调仓确认（T 日信号 → T+confirm_lag 执行）──
        due = [t for t in pending if t[0] == today]
        for _, target in due:
            if not target:
                # 空目标 = 信号明确要求清仓（如回撤闸门/异常状态）→ 全部赎回
                if pos:
                    n_reb += 1
                for code, lots in list(pos.items()):
                    nav = _nav_on(code, today)
                    if nav is None:
                        continue
                    gross = sum(l.shares * nav for l in lots)
                    cost = 0.0
                    for l in lots:
                        hd = (today - l.conf_date).days
                        r = fee.redeem_lte7d if hd < 7 else (
                            fee.redeem_lte30d if hd < 30 else (
                                fee.redeem_lte365d if hd < 365 else fee.redeem_gt365d))
                        cost += l.shares * nav * r
                    costs_total += cost
                    cash_now += gross - cost
                pos = {}
                holdings_history[today] = []
                continue
            target = [c for c in target if _nav_on(c, today) is not None]
            if not target:
                continue
            n_reb += 1
            holdings_history[today] = list(target)
            # 总市值（按最近净值）
            port_val = cash_now
            last_nav: dict[str, float] = {}
            for code, lots in pos.items():
                nav = _nav_on(code, today)
                if nav is not None:
                    last_nav[code] = nav
                    port_val += sum(l.shares * nav for l in lots)
            tgt_w = 1.0 / len(target)
            new_pos: dict[str, list[_Lot]] = {}
            # 卖出不在目标的
            for code, lots in list(pos.items()):
                if code in target:
                    continue
                nav = last_nav.get(code) or _nav_on(code, today)
                if nav is None:
                    continue  # 无净值（停发）：顺延持有
                gross = sum(l.shares * nav for l in lots)
                cost = 0.0
                for l in lots:
                    hd = (today - l.conf_date).days
                    r = fee.redeem_lte7d if hd < 7 else (
                        fee.redeem_lte30d if hd < 30 else (
                            fee.redeem_lte365d if hd < 365 else fee.redeem_gt365d))
                    cost += l.shares * nav * r
                costs_total += cost
                cash_now += gross - cost
            pos = {c: l for c, l in pos.items() if c in target}
            # 再平衡/买入：按目标权重
            for code in target:
                nav = _nav_on(code, today)
                if nav is None:
                    continue
                is_etf = code.startswith("etf:")
                cur_val = sum(l.shares * nav for l in pos.get(code, []))
                want = port_val * tgt_w
                if want <= cur_val * 1.001:      # 减仓（漂移）→ 赎回差额
                    diff = cur_val - want
                    if diff <= 0:
                        continue
                    lots = pos[code]
                    sell_sh = min(diff / nav, sum(l.shares for l in lots))
                    # 从旧槽先卖（持有期长 → 费率低）
                    lots.sort(key=lambda l: l.conf_date)
                    remain = sell_sh
                    for l in list(lots):
                        take = min(remain, l.shares)
                        hd = (today - l.conf_date).days
                        r = (0.0 if is_etf else fee.redeem_rate(hd))
                        c_ = take * nav * (r + (fee.etf_slippage if is_etf else 0.0))
                        costs_total += c_
                        cash_now += take * nav - c_
                        remain -= take
                        l.shares -= take
                        if l.shares <= 1e-9:
                            lots.remove(l)
                    if not lots:
                        pos.pop(code, None)
                else:                             # 增仓 → 申购
                    amt = want - cur_val
                    if amt < 1.0:
                        continue
                    if is_etf:
                        c_ = amt * (fee.etf_commission + fee.etf_slippage)
                        net_amt = amt - c_
                        sh = net_amt / nav
                    else:
                        net_amt = amt / (1 + fee.subscribe)   # 外扣法
                        c_ = amt - net_amt
                        sh = net_amt / nav
                    costs_total += c_
                    cash_now -= amt
                    pos.setdefault(code, []).append(
                        _Lot(shares=sh, conf_date=today, kind="etf" if is_etf else "open"))
        pending = [t for t in pending if t[0] > today]

        # ── 2) T 日信号登记（明日/滞后 N 日确认）──
        if today in scores_by_reb:
            s = scores_by_reb[today].dropna()
            target = list(s.sort_values(ascending=False).head(top_n).index)
            conf = None
            if confirm_lag == 0:
                conf = today
            else:
                future = [d for d in all_dates[di:] if d > today]
                conf = future[confirm_lag - 1] if len(future) >= confirm_lag else None
            if conf is not None:
                pending.append((conf, target))

        # ── 3) 逐日估值 ──
        total = cash_now
        for code, lots in pos.items():
            nav = _nav_on(code, today)
            if nav is not None:
                total += sum(l.shares * nav for l in lots)
        equity_curve[today] = total

    eq = pd.Series(equity_curve).sort_index()
    b = bench_close
    if isinstance(b, pd.Series) and not b.empty:
        b = b.dropna()
        b.index = pd.to_datetime(b.index)
        b = b.reindex(eq.index).ffill()
    res = FundBacktestResult(equity=eq, bench=b if isinstance(b, pd.Series) else pd.Series(dtype=float),
                             holdings_history=holdings_history,
                             turnover_costs=costs_total, n_rebalances=n_reb)
    return res
