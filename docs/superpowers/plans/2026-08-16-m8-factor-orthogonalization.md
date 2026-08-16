# 收尾③:情绪/热点因子残差正交化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 舆情因子与热点因子对**已有基础因子残差正交化**(spec §5.1/§5.2),剔除与价值/成长/质量/动量/低波重叠的部分,只保留独立增量信息,避免重复计价。

**Architecture:** 纯确定性,改 `engine/factors.py`:`FactorDef` 加 `orthogonalize` 标志;`score_factors` 先算各类别得分,再把「正交类别」(hotspot/sentiment)对「基础类别」得分矩阵做 OLS 残差 + 重标准化,用残差进合成。新增纯函数 `orthogonalize(y, X)`。engine 无 LLM。

**Tech Stack:** Python 3.10+、pandas、numpy、pytest。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md` §5.1(热点对已有因子正交化)、§5.2(AI 舆情正交因子)。

## Global Constraints

- **确定性**:相同输入恒等输出;残差用 `np.linalg.lstsq`(与 `neutralize` 同法)。
- **不破坏**:基础因子行为不变;正交类别缺基础因子/样本不足时优雅退化(原样返回)。
- **engine 无 LLM**:守护通过。
- **提交规范**:footer 两行;分支 `claude/superpower-brainstorming-grill-me-amuwan`。

---

### Task 1: `orthogonalize` 纯函数

**Files:** Modify `src/aselect/engine/factors.py`;Test `tests/test_factor_neutralization.py`

**Interfaces:** `orthogonalize(y: pd.Series, X: pd.DataFrame) -> pd.Series`(y 对 X 各列 + 截距做 OLS,取残差;样本/列不足则原样返回;结果不再标准化,由调用方决定)。

- [ ] **Step 1** 写失败测试:`y = 2*x1 - x2 + 噪声`(x1,x2 为 X 两列),`r=orthogonalize(y,X)`,`corr(r, x1)≈0`、`corr(r, x2)≈0`;X 为空 → 原样返回。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现(mask NaN;`mask.sum() < X.shape[1]+2` 则原样返回;残差写回对齐 index)。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 提交 `feat(engine): orthogonalize 残差正交化纯函数`。

---

### Task 2: `FactorDef.orthogonalize` + `score_factors` 接入

**Files:** Modify `src/aselect/engine/factors.py`;Test `tests/test_factor_neutralization.py`、`tests/test_sentiment_factor.py`

**Interfaces:** `FactorDef(..., orthogonalize: bool = False)`;`DEFAULT_FACTORS` 的 hotspot/sentiment 置 `orthogonalize=True`。`score_factors`:算完各 `cat_scores` 后,对「正交类别」(该类所有 def orthogonalize=True)用 `orthogonalize` 对基础类别得分矩阵取残差 → `zscore` → 覆盖该类别得分,再合成。

- [ ] **Step 1** 写失败测试:构造 sentiment 与某基础因子高度相关的截面 → 未正交时 `score_sentiment` 与该基础因子高相关;正交后二者相关≈0;且 `total_score` 仍确定性、无 NaN。基础因子得分不受影响。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现(区分 base/orth 类别;orth 对 base 矩阵残差;无 base 则跳过)。
- [ ] **Step 4** 跑测试 + `test_no_llm_in_core` + 既有因子/情绪/热点测试全绿。
- [ ] **Step 5** 全量 `pytest -q` 绿;提交 `feat(engine): 热点/情绪因子对基础因子残差正交化`。

---

## Self-Review

**1. Spec coverage(§5.1/§5.2):** 热点与情绪对已有因子残差正交化(Task 2);纯确定性(Task 1)。
**2. Placeholder scan:** 各 Step 含断言/最小实现;无 TBD/TODO。
**3. Type consistency:** `orthogonalize(y,X)->Series`、`FactorDef(...,orthogonalize=False)` 跨 Task 一致。

## 完成记录(2026-08-16)
- 2 任务 TDD 落地,全仓 158 测试通过,已推送(PR #1)。
- `orthogonalize(y,X)` 纯函数 + `FactorDef.orthogonalize`;score_factors 把热点/情绪对基础类别得分残差化 + 重标准化。
- 验证:sentiment=2*roe+indep 时,正交后 score_sentiment 与 score_quality 相关≈0(<0.15),独立部分方向为正未被抹平;既有情绪/热点/回测测试全绿。

## 后续
- 情绪/热点单因子在正交化后重验 IC(需历史新闻夹具)。
- 真实财报 PIT(需真实环境,见 m7 计划)。
