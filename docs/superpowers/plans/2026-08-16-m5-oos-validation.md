# M5:样本外一次性验收 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把事件驱动摆动回测接入样本外纪律(铁律3):**训练段拟合 IC 权重,样本外段只测一次**,报 IC/夏普/期望/回撤/盈亏比,收官整个 spec。

**Architecture:** 薄封装,复用 `run_factor_research`(训练段 IC)、`ic_category_weights`(权重)、`run_swing_backtest`(train/OOS 两段各跑一次)。新增 `run_validated_swing`(镜像既有 `run_validated_strategy`),CLI `swing --oos`。

**Tech Stack:** Python 3.10+、pandas、pytest;无新依赖。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md` §7.3(walk-forward + 单一 OOS)、§12(验收标准)。

## Global Constraints

- **样本外只测一次**:OOS 段为对外头条指标,禁止在其上反复调参。
- **训练/样本外不重叠**:split_date 前=训练,后=样本外。
- **不做胜率验收**:报 IC/夏普/期望/回撤/盈亏比;胜率仅参考。
- **确定性**;engine 无 LLM(守护)。
- **提交规范**:footer 两行;分支 `claude/superpower-brainstorming-grill-me-amuwan`。

---

### Task 1: `run_validated_swing`

**Files:** Modify `src/aselect/runner.py`;Test `tests/test_swing_backtest.py`

**Interfaces:**
```python
def run_validated_swing(store, config, freq="W", top_n=10, max_per_industry=2,
                        oos_split=0.7) -> dict:
    # {split_date, weights, train: SwingReport, oos: SwingReport}
    # 训练段 run_factor_research(end=split)→ic_category_weights→权重；
    # train=run_swing_backtest(...end=split, weights)；oos=run_swing_backtest(...start=split, weights)
    # 样本太短返回 {"error": ...}
```

- [ ] **Step 1** 写失败测试:seed 合成库(days≥260)→ `run_validated_swing(oos_split=0.7)` 返回含 `split_date/weights/train/oos`;train 与 oos 均为 `SwingReport`,`n_trades≥0`;两段各自有限指标;确定性(两次相等)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现(镜像 `run_validated_strategy`,把 `run_strategy_backtest` 换成 `run_swing_backtest`,透传 `max_per_industry`)。
- [ ] **Step 4** 跑测试 + `test_no_llm_in_core` 通过。
- [ ] **Step 5** 提交 `feat(runner): run_validated_swing（摆动回测样本外纪律）`。

---

### Task 2: CLI `swing --oos`

**Files:** Modify `src/aselect/cli.py`;(手测)

**Interfaces:** `swing` 子命令加 `--oos`(float,默认 0);>0 时走 `run_validated_swing`,分别打印训练段与「样本外(只测一次)」的期望/盈亏比/夏普/回撤。

- [ ] **Step 1** 加 `--oos` 分支到 `_swing`:打印切分日 + IC 权重 + 两段指标(标注「样本外·只测一次」)。
- [ ] **Step 2** `python -m aselect.cli seed && python -m aselect.cli swing --oos 0.7` 手测出两段报告。
- [ ] **Step 3** 全量 `pytest -q` 绿;提交 `feat(cli): swing --oos 样本外验收`。

---

## Self-Review

**1. Spec coverage(§7.3/§12):** 训练段拟合权重 + OOS 只测一次(Task 1);报 IC/夏普/期望/回撤/盈亏比(Task 2 打印);不做胜率验收。
**2. Placeholder scan:** 各 Step 含具体断言/最小实现;无 TBD/TODO。
**3. Type consistency:** `run_validated_swing(...)->dict{split_date,weights,train,oos}` 与既有 `run_validated_strategy` 一致;train/oos 为 `SwingReport`。

## M5 完成记录(2026-08-16)
- 2 任务 TDD 落地,全仓 144 测试通过,已推送(PR #1)。
- `run_validated_swing`(镜像 run_validated_strategy)+ CLI `swing --oos`。
- 手测 `swing --oos 0.7`:切分 2025-10-10,训练段 +2.05%(期望 +0.0005),样本外 −6.26%(期望 −0.0011)——正是 OOS 纪律要暴露的「训练段过拟合噪声、样本外回吐」的诚实数字。
- 至此 spec M1–M5 全部完成。真实数字待接入 akshare 真实数据。

## 后续(spec 收官后)
- 合成源历史季报快照 → 让训练段 IC 覆盖基本面/热点/情绪因子(当前主要是价格因子)。
- 真实 akshare 数据接入后重跑 OOS 验收,数字方有实盘意义。
