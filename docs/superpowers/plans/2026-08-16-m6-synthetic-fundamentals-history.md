# 收尾①:合成源历史季报快照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `SyntheticSource.fundamentals()` 产出**多期季报快照**(真实 `ann_date`、严格 PIT),使 PIT 历史回测能拿到基本面/行业数据,**解锁基本面·热点·情绪因子的历史 IC 验证**,并使 `run_factor_research` 中的 `industry_neutral` 标志变得可观测(M1 执行时发现的夹具局限,用户已拍板修此项)。

**Architecture:** 仅改合成源:`fundamentals()` 从「今日单快照」改为「近 N 年逐季度快照」,每期 `date`=报告期、`ann_date`=披露日(报告期 + ~1 月 lag,且 ≤ 今日)、`industry` 恒定、财务指标按 (symbol, 期) 确定性微漂移。存储 PK `(symbol, date)` 天然多行入库;`get_fundamentals(as_of)` 按 `ann_date` 过滤(已支持);`build_cross_section` 取「已披露中报告期最新」一条(已支持)。**不改** engine / storage / pipeline。

**Tech Stack:** Python 3.10+、pandas、numpy、pytest。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md` §4.2(PIT 防前视);M1 计划「后续」首条(已决)。

## Global Constraints

- **严格 PIT 无前视**:`ann_date` = 披露日,且 `ann_date ≤ 今日`;`ann_date ≥ date`(披露晚于报告期)。
- **确定性**:值按 (symbol, 报告期) 固定 seed 派生,相同输入恒等输出。
- **非破坏**:现有直接调用 `fundamentals()` 的 smoke 测试(determinism/degradation/neutralization)只查确定性/无错,多行仍通过;seed/sync 经 PK 去重入库多期。
- **提交规范**:footer 两行;分支 `claude/superpower-brainstorming-grill-me-amuwan`。

---

### Task 1: 合成源多期季报快照

**Files:** Modify `src/aselect/datasource/synthetic_source.py`;Test `tests/test_pit.py`(或新 `tests/test_synthetic_fundamentals.py`)

**Interfaces:** `SyntheticSource(fund_quarters:int=12).fundamentals(symbols=None)` 返回每只 `fund_quarters` 行(逐季),列同旧(symbol,date,ann_date,industry,pe…total_mv);`date`=季度末、`ann_date`=季度末+~30 日且 ≤ 今日。

- [ ] **Step 1** 写失败测试:`fundamentals()` 每只 symbol 多于 1 行(逐季);每行 `ann_date >= date` 且 `ann_date <= 今日`;`industry` 每只恒定;确定性(两次 equal);列集合与旧一致。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现:生成近 N 年季度末列表,逐期用 `default_rng(seed + crc(symbol) + 期序)` 派生值,`industry` 用 `_INDUSTRY`。剔除 `ann_date > 今日` 的期。
- [ ] **Step 4** 跑测试通过 + 既有 `test_determinism`/`test_degradation`/`test_factor_neutralization`/`test_pit` 全通过(非破坏验证)。
- [ ] **Step 5** 提交 `feat(datasource): 合成源产出多期季报快照(真实 ann_date,PIT)`。

---

### Task 2: 历史 IC 解锁验证(回报测试)

**Files:** Test `tests/test_factor_research.py`(或 `tests/test_hotspot.py`)

- [ ] **Step 1** 写测试:seed 合成库(现含历史季报)→ `run_factor_research(freq="M")`,断言**基本面因子**(如 `roe`)`n > 0` 且 `ic_mean` 有限(此前因今日单快照恒为 0);并断言 `hotspot`(依赖 industry)现在 `n > 0`(M1 遗留的历史 IC 现已可算)。
- [ ] **Step 2** 跑测试(定位/微调后)通过。
- [ ] **Step 3** 全量 `pytest -q` 绿;更新 M1 计划「后续」标注此项已完成;提交 `test: 历史季报解锁基本面/热点因子历史 IC`。

---

## Self-Review

**1. Spec coverage:** PIT 多期披露(Task 1)、历史 IC 解锁(Task 2)、industry_neutral 可观测(随 hotspot n>0 间接覆盖)。
**2. Placeholder scan:** 各 Step 含断言/最小实现;无 TBD/TODO。
**3. Type consistency:** `fundamentals()` 列集合不变;仅行数变多。

## 完成记录(2026-08-16)
- 2 任务 TDD 落地,全仓 150 测试通过,已推送(PR #1)。
- `fundamentals()` 改逐季历史快照(真实 ann_date、PIT);非破坏(既有消费者全绿)。
- 回报验证:`run_factor_research` 现产出 `roe` n=34、`hotspot` n=32(此前均为 0),历史 IC 解锁,`industry_neutral` 标志可观测。

## 后续
- 接真实 akshare `stock_financial_*`(真实披露日)→ 真数据阶段。
- 情绪历史新闻夹具(让情绪因子也有历史 IC)→ 需要时再做。
