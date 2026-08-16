# M4:AI 舆情正交因子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AI 舆情情绪作为**正交因子**纳入打分:AI 只在文本输入端产出情绪分 → 结构化落地(带披露日 as_of 防前视)→ 作为普通数值因子被 engine 消费。**绝不让 AI 直接预测涨跌**;默认关闭,缺 Key 回退 NullAnalyzer,核心照跑。

**Architecture:** 复用既有 `AIAnalyzer.analyze_sentiment`/`NullAnalyzer`/`get_analyzer` 与 features 表(sentiment/confidence/as_of)+ `build_cross_section` 已合并 sentiment 列。新增:①`engine/factors.py` 注册 `sentiment` 因子(读数值列,engine 无 LLM);②`datasource` 加 `news(symbol)` 接口(默认空,合成源空);③`data/sentiment.py` 编排 `build_sentiment_features`(新闻→analyzer→情绪→按 as_of 入库);④CLI `sentiment`。AI 仅在 ③ 的输入端被调用,engine/因子只读数值。

**Tech Stack:** Python 3.10+、pandas、pytest;复用现有 ai/ 适配器与 storage features 表;不引入新依赖。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md` §5.2(AI 舆情正交因子)、§1 铁律1(engine 无 LLM)、§6(热插拔)。

## Global Constraints

- **确定性防火墙**:`sentiment` 因子在 engine 内只读数值列;**AI 调用只在 `data/sentiment.py` 输入端**;`test_no_llm_in_core` 持续通过。
- **AI 不预测方向**:AI 仅产 [-1,1] 情绪分 + 置信度,落为结构化特征;**严禁**让 AI 输出买卖/涨跌判断。
- **防前视**:情绪特征 `as_of` = 新闻披露日;回测按 as_of 过滤(features 表已支持)。
- **热插拔**:`ai.enabled=false` / `ai.features.sentiment` 关 / 缺 Key → NullAnalyzer(情绪 0/置信 0),因子退化为中性,核心照跑。
- **提交规范**:footer 两行;分支 `claude/superpower-brainstorming-grill-me-amuwan`。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/aselect/engine/factors.py` | 因子库 | 改:注册 `sentiment` 因子 |
| `src/aselect/datasource/base.py` | 数据源抽象 | 改:加 `news(symbol)->list[dict]` 默认空 |
| `src/aselect/datasource/synthetic_source.py` | 合成源 | 改:`news` 返回 []（离线无新闻） |
| `src/aselect/data/sentiment.py` | 舆情特征编排(AI 输入端) | 建 |
| `src/aselect/cli.py` | `sentiment` 子命令 | 改 |
| `tests/test_sentiment_factor.py` | M4 单测 | 建 |

---

### Task 1: 注册 `sentiment` 正交因子

**Files:** Modify `src/aselect/engine/factors.py`;Test `tests/test_sentiment_factor.py`

**Interfaces:** `DEFAULT_FACTORS["sentiment"] = [FactorDef("sentiment", "sentiment", ascending=False)]`(情绪越高越好;默认行业/市值中性,保持与风格正交)。

- [ ] **Step 1** 写失败测试:含 `sentiment` 列的截面 `score_factors` 产出 `score_sentiment`;`"sentiment" in DEFAULT_FACTORS`;情绪高的股票 `score_sentiment` 更高。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现:`DEFAULT_FACTORS` 加 `sentiment` 条目。
- [ ] **Step 4** 跑测试 + `test_no_llm_in_core` 通过。
- [ ] **Step 5** 提交 `feat(engine): 注册 sentiment 正交因子（读数值列，无 LLM）`。

---

### Task 2: 数据源 `news` 接口

**Files:** Modify `src/aselect/datasource/base.py`、`synthetic_source.py`;Test `tests/test_sentiment_factor.py`

**Interfaces:** `DataSource.news(self, symbol: str) -> list[dict]`(默认 `return []`);每条 `{"text": str, "date": "YYYY-MM-DD"}`。合成源返回 `[]`。

- [ ] **Step 1** 写失败测试:`SyntheticSource().news("600519") == []`;`DataSource` 有 `news` 方法(默认空)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现:base 加默认 `news`;合成源 override 返 []。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 提交 `feat(datasource): news(symbol) 接口（默认空，合成源空）`。

---

### Task 3: 舆情特征编排 `build_sentiment_features`

**Files:** Create `src/aselect/data/sentiment.py`;Test `tests/test_sentiment_factor.py`

**Interfaces:**
```python
def build_sentiment_features(store, config, symbols, *, analyzer=None,
                             news_fn=None) -> int:
    """对每只:news_fn(symbol)→[{text,date}] → analyzer.analyze_sentiment(texts)
    → 按置信度加权聚合出 (sentiment, confidence) → upsert_features
    (symbol, date=最新新闻日, sentiment, confidence, as_of=最新新闻日, source)。
    analyzer 缺省用 get_analyzer(config)；news_fn 缺省用数据源 .news。
    返回写入的 symbol 数。无新闻/NullAnalyzer 中性 → 跳过或写中性，核心照跑。"""
```

- [ ] **Step 1** 写失败测试:注入假 `news_fn`(给 A 正面文本、B 负面文本)+ 假 analyzer(正文本→+0.8、负→−0.6);跑后 `store.get_features` 含 A/B,sentiment 符号正确,as_of=新闻日;NullAnalyzer 时不产出正情绪(全 0/跳过),核心不报错。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现(置信度加权聚合;无新闻跳过;PIT as_of=最新新闻日)。
- [ ] **Step 4** 跑测试 + 守护通过。
- [ ] **Step 5** 提交 `feat(data): build_sentiment_features（AI 输入端→结构化情绪入库）`。

---

### Task 4: 端到端 —— 情绪因子进打分

**Files:** Test `tests/test_sentiment_factor.py`(编排;实现已在前序任务)

- [ ] **Step 1** 写测试:seed 合成库 → `build_sentiment_features`(假 analyzer 给某只正情绪)→ `build_cross_section` → `score_factors`,该只 `score_sentiment` 明显更高;`ai.enabled=false` 路径下 `build_sentiment_features` 用真实工厂(NullAnalyzer)不产出正情绪、`score_factors` 照常跑。
- [ ] **Step 2** 跑测试(定位缺口,补齐后)通过。
- [ ] **Step 3** 全量 `pytest -q` 绿。
- [ ] **Step 4** 提交 `test: 情绪因子端到端（输入端→入库→打分）`。

---

### Task 5: CLI `sentiment` 子命令

**Files:** Modify `src/aselect/cli.py`;(手测)

- [ ] **Step 1** 加 `sentiment` 子命令:`build_sentiment_features(store, cfg, universe)`(用配置的 analyzer + 数据源 news),打印写入条数;默认 ai 关 → NullAnalyzer,提示「AI 未启用,情绪为中性」。
- [ ] **Step 2** `python -m aselect.cli seed && python -m aselect.cli sentiment` 手测(合成源无新闻 → 0 条,核心不崩)。
- [ ] **Step 3** 全量 `pytest -q` 绿;提交 `feat(cli): sentiment 子命令`。

---

## Self-Review

**1. Spec coverage(§5.2):** AI 只在输入端(Task 3)、结构化入库带 as_of 防前视(Task 3)、作为正交数值因子(Task 1)、不预测方向(analyzer 只出情绪分)、缺 Key/关闭回退 NullAnalyzer 核心照跑(Task 3/4)、engine 无 LLM(Task 1 + 守护)。
**2. Placeholder scan:** 各 Task Step 含具体断言与最小实现方向;无 TBD/TODO。
**3. Type consistency:** `FactorDef("sentiment","sentiment",...)`、`news(symbol)->list[dict]`、`build_sentiment_features(store,config,symbols,*,analyzer,news_fn)->int`、features 列 `sentiment/confidence/as_of` 跨 Task 一致。

## 后续(不属 M4)
- 真实 akshare 新闻抓取(`AkshareSource.news`,网络)→ 接入真实数据阶段。
- 情绪对已有因子的**真正残差正交化**(当前用标准行业/市值中性近似)→ 多因子研究增强。
- 情绪单因子 walk-forward IC 验证 → 需真实历史新闻或合成历史新闻夹具(与 M1 遗留的历史快照一并做)。
