# M2a:策略纪律规则(反追高入场闸门 + 反卖飞离场) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 aselect 确定性核心新增纯函数的**反追高入场闸门**与**反卖飞离场纪律**(含 ATR 指标),正面对治用户两大风险,完全可单测。

**Architecture:** `engine/indicators.py` 增 ATR;新建 `engine/strategy_rules.py`,含 `entry_gate()`(逐条闸门:日内涨幅/均线偏离/右侧确认/RSI 过热)与逐仓离场状态机 `evaluate_exit()`(ATR 吊灯移动止损 + 硬止损 + 跌破 MA10 趋势离场 + 2R 分批止盈 + 最大持仓)。全部纯函数、确定性、engine 内**无 LLM**;本里程碑不碰回测集成(M2b)。

**Tech Stack:** Python 3.10+、pandas、numpy、pytest;指标优先 pandas-ta,不可用回退向量化(沿用 `indicators.py` 既有模式)。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md`(实现 §6.1 入场闸门、§6.2 离场纪律;§7 回测集成属 M2b)

## Global Constraints

- **确定性防火墙**:`engine/strategy_rules.py` 在 engine 内,**严禁 import `aselect.ai`**;`tests/test_no_llm_in_core.py` 必须持续通过。
- **纯函数确定性**:相同输入恒得相同输出;不读文件/网络/时钟。
- **阈值为常识固定初值**(§Q2 决策):`GateParams`/`ExitParams` 默认值即 spec §6 表格数值;训练段仅粗检验,不在此精调。
- **方向即语义**:闸门未过=不入场;离场决策优先级 hard_stop > trailing > trend > scale_out > max_hold。
- **提交规范**:commit message 结尾附仓库要求两行 footer;分支 `claude/superpower-brainstorming-grill-me-amuwan`。
- **不做胜率优化**:规则以控制追高成本、放大右侧盈亏比为目的,验收看回测期望/盈亏比(M2b),非胜率。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/aselect/engine/indicators.py` | 技术指标 | 改:`add_indicators` 增 `atr14`;新增 `_atr` 助手(pandas-ta 优先+回退) |
| `src/aselect/engine/strategy_rules.py` | 入场闸门 + 离场状态机(纯函数) | 建 |
| `tests/test_indicators.py` | 指标对拍 | 改:加 ATR 用例 |
| `tests/test_strategy_rules.py` | 闸门/离场单测 | 建 |

---

### Task 1: ATR 指标

**Files:**
- Modify: `src/aselect/engine/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Produces: `add_indicators(df)` 输出增列 `atr14`;`_atr(high, low, close, n=14) -> pd.Series`(Wilder 平滑真实波幅)。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_indicators.py`:

```python
def test_atr_matches_wilder_definition():
    # 已知序列：TR=max(H-L, |H-prevC|, |L-prevC|)，ATR=Wilder(TR, 14)
    closes = list(np.linspace(10, 30, 60))
    out = add_indicators(_df(closes))
    assert "atr14" in out.columns
    atr = out["atr14"].dropna()
    assert (atr > 0).all()                    # 波幅恒正
    assert atr.notna().sum() >= len(closes) - 20

def test_atr_zero_when_flat():
    closes = [20.0] * 40
    df = pd.DataFrame({
        "date": pd.bdate_range("2020-01-01", periods=40).strftime("%Y-%m-%d"),
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1e6] * 40,
    })
    out = add_indicators(df)
    assert float(out["atr14"].dropna().iloc[-1]) == 0.0   # 无波动 → ATR=0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_indicators.py -k atr -v`
Expected: FAIL(`atr14` 不存在)

- [ ] **Step 3: 实现** — `indicators.py`:
  `add_indicators` 在 `out["rsi14"] = ...` 后加:
  ```python
      out["atr14"] = _atr(high, low, close, 14)
  ```
  文件末尾(`backend()` 前)加助手:
  ```python
  def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
      if _HAS_PTA:
          return _pta.atr(high, low, close, length=n)
      prev_close = close.shift(1)
      tr = pd.concat([(high - low),
                      (high - prev_close).abs(),
                      (low - prev_close).abs()], axis=1).max(axis=1)
      # Wilder 平滑（与 RSI 一致）
      return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
  ```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS(含新旧用例)

- [ ] **Step 5: 提交**

```bash
git add src/aselect/engine/indicators.py tests/test_indicators.py
git commit  # feat(engine): ATR 指标（吊灯止损用）
```

---

### Task 2: 反追高入场闸门 `entry_gate`

**Files:**
- Create: `src/aselect/engine/strategy_rules.py`
- Test: `tests/test_strategy_rules.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class GateParams:
      max_intraday_gain: float = 0.03   # 当日涨幅上限（超过=追高）
      near_limit: float = 0.095         # 接近涨停阈值
      max_ext_ma20: float = 0.15        # 偏离 MA20 上限
      rsi_overheat: float = 70.0        # RSI 过热
  @dataclass
  class GateResult:
      passed: bool
      checks: dict[str, bool]           # 各闸门是否通过（True=过）
  def entry_gate(bars: pd.DataFrame, params: GateParams = GateParams()) -> GateResult
  ```
  `bars`：该 symbol 截至信号日 T 的日线(已排序,含 close/high/low;内部自算指标)。取最后一行为 T。

闸门(全过 → passed=True):
- `intraday`:`close_T/close_{T-1}-1 <= max_intraday_gain` 且 `< near_limit`
- `not_extended`:`close_T/ma20_T - 1 <= max_ext_ma20`
- `right_side`:`close_T > ma5_T`(右侧,不接飞刀)
- `rsi_ok`:`rsi14_T <= rsi_overheat`

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_strategy_rules.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd

from aselect.engine.strategy_rules import GateParams, entry_gate


def _bars(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs or [c * 1.005 for c in closes]
    lows = lows or [c * 0.995 for c in closes]
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [1e6] * n,
    })


def test_gate_passes_clean_right_side_entry():
    # 平缓上行、末日温和上涨、站上 MA5、RSI 不过热
    closes = list(np.linspace(10, 12, 40)) + [12.05]
    res = entry_gate(_bars(closes))
    assert res.passed is True
    assert all(res.checks.values())


def test_gate_rejects_intraday_surge():
    closes = list(np.linspace(10, 12, 40)) + [12 * 1.06]   # 末日 +6% 追高
    res = entry_gate(_bars(closes))
    assert res.passed is False
    assert res.checks["intraday"] is False


def test_gate_rejects_overextended_from_ma20():
    closes = list(np.linspace(10, 11, 40)) + [15.0]        # 远离 MA20
    res = entry_gate(_bars(closes))
    assert res.passed is False
    assert res.checks["not_extended"] is False


def test_gate_rejects_below_ma5_falling_knife():
    closes = list(np.linspace(20, 10, 40)) + [9.0]         # 自由落体、跌破 MA5
    res = entry_gate(_bars(closes))
    assert res.passed is False
    assert res.checks["right_side"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_strategy_rules.py -k gate -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现** — 新建 `src/aselect/engine/strategy_rules.py`(先写文件头 + GateParams/GateResult/entry_gate):

```python
"""策略纪律规则（纯函数，确定性核心，AI 禁区）。

反追高入场闸门 + 反卖飞离场纪律。阈值为常识固定初值（见设计 spec §6），
训练段仅粗检验、不精调。不读文件/网络/时钟；相同输入恒得相同输出。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .indicators import add_indicators


@dataclass(frozen=True)
class GateParams:
    max_intraday_gain: float = 0.03
    near_limit: float = 0.095
    max_ext_ma20: float = 0.15
    rsi_overheat: float = 70.0


@dataclass
class GateResult:
    passed: bool
    checks: dict


def entry_gate(bars: pd.DataFrame, params: GateParams = GateParams()) -> GateResult:
    if len(bars) < 21:                       # 不足以算 MA20 → 保守拒绝
        return GateResult(False, {"insufficient_history": False})
    ind = add_indicators(bars)
    last = ind.iloc[-1]
    prev_close = float(ind["close"].iloc[-2])
    close = float(last["close"])
    intraday = close / prev_close - 1
    ext = close / float(last["ma20"]) - 1 if pd.notna(last["ma20"]) else 0.0

    checks = {
        "intraday": (intraday <= params.max_intraday_gain) and (intraday < params.near_limit),
        "not_extended": ext <= params.max_ext_ma20,
        "right_side": pd.notna(last["ma5"]) and close > float(last["ma5"]),
        "rsi_ok": pd.isna(last["rsi14"]) or float(last["rsi14"]) <= params.rsi_overheat,
    }
    return GateResult(passed=all(checks.values()), checks=checks)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_strategy_rules.py -k gate -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aselect/engine/strategy_rules.py tests/test_strategy_rules.py
git commit  # feat(engine): 反追高入场闸门 entry_gate
```

---

### Task 3: 反卖飞离场状态机 `evaluate_exit`

**Files:**
- Modify: `src/aselect/engine/strategy_rules.py`
- Test: `tests/test_strategy_rules.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ExitParams:
      chandelier_k: float = 3.0      # 吊灯止损：最高收盘 − k×ATR
      hard_stop_atr: float = 2.0     # 硬止损：入场价 − n×ATR（初始风险 R=hard_stop_atr×ATR）
      trend_ma: int = 10             # 跌破 MA10 趋势离场
      scale_out_R: float = 2.0       # 盈利达 2R 分批
      scale_out_frac: float = 0.5    # 减仓比例
      max_hold: int = 20             # 最大持仓交易日
  @dataclass
  class PositionState:
      entry_price: float
      atr_at_entry: float
      highest_close: float
      days_held: int = 0
      scaled_out: bool = False
      remaining: float = 1.0
  @dataclass
  class ExitDecision:
      action: str        # "none" | "scale_out" | "exit"
      reason: str = ""
      fraction: float = 0.0
  def evaluate_exit(state: PositionState, bar: dict, params: ExitParams = ExitParams()) -> ExitDecision
  ```
  `bar`：当日 `{"close":..., "ma10":..., "atr":...}`。函数**推进** state(更新 `highest_close`、`days_held`),再按优先级 hard_stop > trailing > trend > scale_out > max_hold 返回决策。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_strategy_rules.py`:

```python
from aselect.engine.strategy_rules import (
    ExitParams, PositionState, evaluate_exit,
)


def _state(entry=100.0, atr=2.0):
    return PositionState(entry_price=entry, atr_at_entry=atr, highest_close=entry)


def test_exit_hard_stop_triggers_first():
    st = _state()
    d = evaluate_exit(st, {"close": 95.0, "ma10": 99.0, "atr": 2.0})  # < 100-2*2=96
    assert d.action == "exit" and d.reason == "hard_stop"


def test_exit_trailing_chandelier():
    st = _state()
    evaluate_exit(st, {"close": 120.0, "ma10": 110.0, "atr": 2.0})    # 抬高 highest=120
    d = evaluate_exit(st, {"close": 113.0, "ma10": 112.0, "atr": 2.0})  # <120-3*2=114
    assert d.action == "exit" and d.reason == "trailing_stop"


def test_exit_trend_break_below_ma10():
    st = _state()
    d = evaluate_exit(st, {"close": 101.0, "ma10": 102.0, "atr": 2.0})  # 未触止损但跌破 MA10
    assert d.action == "exit" and d.reason == "trend_break"


def test_scale_out_at_2R_once():
    st = _state(entry=100.0, atr=2.0)     # R=hard_stop_atr*atr=2*2=4 → 2R=+8 → 108
    d1 = evaluate_exit(st, {"close": 109.0, "ma10": 105.0, "atr": 2.0})
    assert d1.action == "scale_out" and abs(d1.fraction - 0.5) < 1e-9
    assert st.scaled_out is True and abs(st.remaining - 0.5) < 1e-9
    # 再次到位不重复减仓
    d2 = evaluate_exit(st, {"close": 110.0, "ma10": 106.0, "atr": 2.0})
    assert d2.action != "scale_out"


def test_exit_max_hold():
    st = _state()
    st.days_held = 19
    d = evaluate_exit(st, {"close": 101.0, "ma10": 100.0, "atr": 2.0})  # 满 20 日
    assert d.action == "exit" and d.reason == "max_hold"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_strategy_rules.py -k "exit or scale" -v`
Expected: FAIL(未实现)

- [ ] **Step 3: 实现** — 追加到 `strategy_rules.py`:

```python
@dataclass(frozen=True)
class ExitParams:
    chandelier_k: float = 3.0
    hard_stop_atr: float = 2.0
    trend_ma: int = 10
    scale_out_R: float = 2.0
    scale_out_frac: float = 0.5
    max_hold: int = 20


@dataclass
class PositionState:
    entry_price: float
    atr_at_entry: float
    highest_close: float
    days_held: int = 0
    scaled_out: bool = False
    remaining: float = 1.0


@dataclass
class ExitDecision:
    action: str
    reason: str = ""
    fraction: float = 0.0


def evaluate_exit(state: PositionState, bar: dict,
                  params: ExitParams = ExitParams()) -> ExitDecision:
    close = float(bar["close"])
    atr = float(bar.get("atr") or state.atr_at_entry)
    state.days_held += 1
    state.highest_close = max(state.highest_close, close)
    R = params.hard_stop_atr * state.atr_at_entry     # 初始风险

    # 优先级：hard_stop > trailing > trend > scale_out > max_hold
    if close <= state.entry_price - R:
        return ExitDecision("exit", "hard_stop")
    if close <= state.highest_close - params.chandelier_k * atr:
        return ExitDecision("exit", "trailing_stop")
    ma10 = bar.get("ma10")
    if ma10 is not None and pd.notna(ma10) and close < float(ma10):
        return ExitDecision("exit", "trend_break")
    if (not state.scaled_out
            and close >= state.entry_price + params.scale_out_R * R):
        state.scaled_out = True
        state.remaining = round(state.remaining - params.scale_out_frac, 6)
        return ExitDecision("scale_out", "target_2R", params.scale_out_frac)
    if state.days_held >= params.max_hold:
        return ExitDecision("exit", "max_hold")
    return ExitDecision("none")
```

- [ ] **Step 4: 跑测试确认通过 + 守护**

Run: `pytest tests/test_strategy_rules.py tests/test_no_llm_in_core.py -v`
Expected: PASS(全部;engine 无 LLM 守护通过)

- [ ] **Step 5: 提交**

```bash
git add src/aselect/engine/strategy_rules.py tests/test_strategy_rules.py
git commit  # feat(engine): 反卖飞离场状态机 evaluate_exit（吊灯/硬止损/趋势/分批/超时）
```

---

## Self-Review

**1. Spec coverage(对 §6):**
- §6.1 入场闸门 4 条(日内涨幅/均线偏离/右侧确认/RSI):Task 2 全覆盖。「量能确认」M2a 暂略(需成交量上下文,回测集成时接,记入后续)。⚠️
- §6.2 离场 5 项(吊灯/分批 2R/趋势 MA10/硬止损/最大持仓):Task 3 全覆盖,优先级明确。
- ATR 依赖:Task 1。

**2. Placeholder scan:** 无 TBD/TODO;各步含真实代码与期望输出。

**3. Type consistency:** `GateParams/GateResult/entry_gate`、`ExitParams/PositionState/ExitDecision/evaluate_exit` 跨 Task 一致;`bar` 键 `close/ma10/atr` 一致;`atr14` 列名与 Task 1 一致。

## 后续(不属 M2a)
- 「量能确认」闸门(换手/量比) → M2b 回测集成时接入(截面已有 turnover)。
- 事件驱动周级回测 + 消融对照(有/无闸门期望增量;吊灯 vs 涨停即清盈亏比) → **M2b**。
- 闸门/止损阈值粗敏感性检验 → M2b 回测就位后。
