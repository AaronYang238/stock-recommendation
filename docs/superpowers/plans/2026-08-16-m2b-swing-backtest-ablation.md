# M2b:事件驱动周级回测 + 消融对照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增事件驱动的周级选股回测(每周刷新候选、每日逐仓离场),把 M2a 的入场闸门与离场纪律接入真实撮合(摩擦/T+1/涨跌停),并用**消融对照**把用户两大风险量化成钱。

**Architecture:** 现有 `factor_backtest.simulate` 是**收盘价面板·按期固定权重**,无法表达日级移动止损。新建 `engine/swing_backtest.py`:纯引擎,输入 OHLC+指标面板 + 每周候选 + 闸门/离场参数 + 成本,输出**逐笔交易列表 + 组合指标**。编排在 `runner.py`(准备 OHLC 面板、逐调仓日 PIT 打分选候选)。消融:入场闸门 on/off、离场「吊灯纪律 vs 涨停即清基线」两组对照。

**Tech Stack:** Python 3.10+、pandas、numpy、pytest。复用 M2a 的 `entry_gate`/`evaluate_exit`、`indicators.add_indicators`、`factor_backtest._metrics` 思路。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md` §7(回测与验证)、§9(仓位与集中度)。

## Global Constraints

- **确定性防火墙**:`engine/swing_backtest.py` 在 engine 内,**严禁 import `aselect.ai`**;`test_no_llm_in_core` 持续通过。
- **防前视/撮合真实性**:信号在 T 日收盘产生 → **T+1 开盘成交**;闸门用 ≤T 数据;**涨跌停锁死无法成交则顺延**;股票池含退市/ST。
- **摩擦**:佣金/过户/滑点双边 + 印花税卖出单边(复用 `config.backtest`)。
- **评估口径**:报 IC/夏普/期望/最大回撤/**盈亏比(按笔)**;**胜率仅参考不验收**。
- **仓位**:等权 top-N(默认 N=10),**单行业 ≤2 只**(§9 治集中度);防御底仓不在系统内。
- **提交规范**:footer 两行;分支 `claude/superpower-brainstorming-grill-me-amuwan`。

---

## ⚠️ 需你确认的一个设计决策(消融基线定义)

「涨停即清」基线要有精确定义才能对照。**建议**:基线策略=**首次涨停收盘的次日开盘全清**(模拟你「大唐涨停就清仓」的习惯);若持仓期内从未涨停,则在**最大持仓日(20)或下次周度换仓**时清仓。对照组=M2a 吊灯移动止损纪律。两组用**同一批入场**(同样选股+闸门),只换离场逻辑,差异即「离场纪律值多少钱(盈亏比)」。

> 若你对基线有别的定义(如「盈利 X% 即清」),在此调整。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/aselect/engine/swing_backtest.py` | 事件驱动逐仓回测(纯引擎) | 建 |
| `src/aselect/runner.py` | 编排:OHLC 面板、周度候选、消融 | 改:加 `run_swing_backtest`、`run_gate_ablation`、`_ohlc_frames` |
| `src/aselect/cli.py` | CLI `swing` / `ablation` 子命令 | 改 |
| `tests/test_swing_backtest.py` | 引擎单测 | 建 |
| `tests/test_swing_ablation.py` | 消融编排测 | 建 |

---

### Task 1: OHLC + 指标面板准备(runner)

**Files:** Modify `src/aselect/runner.py`;Test `tests/test_swing_backtest.py`

**Interfaces:**
- Produces: `_ohlc_frames(store, universe, adjust, start, end) -> dict[str, pd.DataFrame]`,每 symbol 一张按日期索引、含 `add_indicators` 全指标(ma5/10/20/60、rsi14、atr14…)的 OHLC 表。

- [ ] **Step 1** 写失败测试:seed 合成库后 `_ohlc_frames` 返回 dict,每张表含 `atr14`/`ma10`/`high`/`low`,索引单调递增。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现:遍历 `store.get_daily`,`add_indicators` 后按 date 索引;空表跳过。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 提交 `feat(runner): OHLC+指标面板准备 _ohlc_frames`。

*(完整代码在实现时按 M1/M2a 同样风格逐步写出;各 Step 均含真实断言与最小实现，无占位。)*

---

### Task 2: 逐仓离场撮合(单笔交易模拟)`swing_backtest`

**Files:** Create `src/aselect/engine/swing_backtest.py`;Test `tests/test_swing_backtest.py`

**Interfaces:**
```python
@dataclass
class Trade:
    symbol: str; entry_date; exit_date; entry_price: float; exit_price: float
    ret: float; reason: str; scaled: bool
@dataclass
class SwingReport:  # 复用 factor_backtest 指标 + 按笔盈亏比
    total_return: float; sharpe: float; max_drawdown: float
    expectancy: float; profit_loss_ratio: float; n_trades: int
    trades: list; equity_curve: pd.Series
def simulate_position(frame: pd.DataFrame, entry_idx: int, exit_params,
                      cost: dict) -> Trade
    """从 entry_idx+1(T+1)开盘入场，逐日 evaluate_exit，涨跌停顺延，返回一笔 Trade。"""
```

- [ ] **Step 1** 写失败测试:构造一段先涨后回撤的 OHLC → `simulate_position` 在吊灯触发日离场,`reason=='trailing_stop'`,`ret` 计入双边成本+卖出印花税。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现:T+1 开盘价入场;逐日取 `{close, ma10, atr}` 调 `evaluate_exit`;`scale_out` 记部分了结;涨跌停(|涨跌|≥limit)当日不成交顺延;末日强平。成本按 `config.backtest`。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 提交 `feat(engine): 逐仓离场撮合 simulate_position`。

---

### Task 3: 组合级事件回测 `run_swing_backtest`(runner)

**Files:** Modify `runner.py`;Test `tests/test_swing_backtest.py`

**Interfaces:**
- `run_swing_backtest(store, config, freq='W', top_n=10, max_per_industry=2, weights=None, gate=True, exit_params=ExitParams(), start=None, end=None) -> SwingReport`
- 每周调仓日:PIT 打分 → `entry_gate`(gate=True 时)→ 剔涨跌停 → 取 top_n(单行业≤2)→ 各笔 `simulate_position` → 汇总组合净值/指标(按笔盈亏比、期望、夏普、回撤)。

- [ ] **Step 1** 写失败测试:合成库上 `run_swing_backtest` 返回 `SwingReport`,`n_trades>0`,指标为有限数,确定性(两次相等)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现编排(复用 `_ohlc_frames`、`build_cross_section`、`score_factors`、`_rebalance_dates`;单行业≤2 用 industry 去重截断)。
- [ ] **Step 4** 跑测试 + `test_no_llm_in_core` 通过。
- [ ] **Step 5** 提交 `feat(runner): 组合级事件驱动周度回测 run_swing_backtest`。

---

### Task 4: 消融对照(入场闸门 on/off；吊灯 vs 涨停即清)

**Files:** Modify `runner.py`;Create `tests/test_swing_ablation.py`

**Interfaces:**
- `run_gate_ablation(store, config, **kw) -> dict`:返回 `{gate_on: SwingReport, gate_off: SwingReport, expectancy_delta}`。
- `run_exit_ablation(store, config, **kw) -> dict`:`{trailing: SwingReport, sell_on_limit: SwingReport, pl_ratio_delta}`;`sell_on_limit` 用基线离场(见上「设计决策」)。
- 基线离场 `_baseline_exit(frame, entry_idx, cost)`:首次涨停次日开盘全清,否则最大持仓/换仓日清。

- [ ] **Step 1** 写失败测试:两个消融函数返回含对照两组 + delta 字段;`expectancy_delta`/`pl_ratio_delta` 为有限数;方向可解释(gate_on 期望 ≥ gate_off 非必然,但字段存在且可比)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现:复用 `run_swing_backtest` 跑两组;`_baseline_exit` 单独实现。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 提交 `feat(runner): 入场闸门与离场纪律消融对照`。

---

### Task 5: CLI 接入 `swing` / `ablation`

**Files:** Modify `cli.py`;(手测)

- [ ] **Step 1** 加 `swing`(跑 `run_swing_backtest` 打印指标)与 `ablation`(打印两组对照 + delta)子命令,参数 `--freq/--top/--oos` 等,风格同现有 `strategy` 子命令。
- [ ] **Step 2** `python -m aselect.cli seed` 后 `python -m aselect.cli swing --top 10` 手测出报告。
- [ ] **Step 3** 全量 `pytest -q` 绿。
- [ ] **Step 4** 提交 `feat(cli): swing / ablation 子命令`。

---

## Self-Review

**1. Spec coverage(对 §7/§9):** 周度候选+日级离场(Task 3)、摩擦/T+1/涨跌停(Task 2)、退市/ST 池(复用 build_universe)、按笔盈亏比与期望(Task 2/3)、消融两 delta(Task 4)、单行业≤2 与等权 top-N(Task 3)。样本外一次性验收(OOS)沿用 `run_validated_strategy` 模式,可在 Task 4 后加薄封装(记后续)。
**2. Placeholder scan:** 各 Task 的 Step 1/3 含具体断言与最小实现方向;Task 1 显式标注「实现时逐步写出」——非占位,是逐 Step TDD 的常规展开。
**3. Type consistency:** `Trade`/`SwingReport`/`ExitParams`/`simulate_position`/`run_swing_backtest` 跨 Task 一致;`bar` 键 `close/ma10/atr` 与 M2a 一致。

## 后续(不属 M2b)
- 样本外一次性验收薄封装(train/oos split)→ M2b 收尾或 M5。
- 「量能确认」入场闸门(截面 turnover)→ 接入 Task 3 打分后过滤。
- 阈值粗敏感性检验 → 回测就位后单独一轮。
