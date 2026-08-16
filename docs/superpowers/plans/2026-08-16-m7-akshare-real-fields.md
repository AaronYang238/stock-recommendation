# 收尾②:akshare 真实数据字段(turnover / net_inflow / news) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `AkshareSource` 补齐 M1/M4 引入的真实字段:`daily()` 增 `turnover`(换手率,`stock_zh_a_hist` 自带)与 `net_inflow`(个股资金流,best-effort);`news(symbol)` 接 `stock_news_em`。让热点因子与舆情因子在真实数据下有料可吃。

**Architecture:** 把列名标准化抽成**模块级纯函数**(`normalize_hist` / `normalize_fund_flow` / `normalize_news`),可在**无 akshare、无网络**下单测;`AkshareSource` 的 `.ak.xxx()` 只是薄包装 + 容错(网络/接口失效不崩,退化为缺列/空)。akshare 延迟导入(`__init__` 内),模块可离线导入以测纯函数。

**Tech Stack:** Python 3.10+、pandas、pytest。akshare 为运行期可选依赖(未装/无网时对应字段优雅缺失)。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md` §4.1(数据字段)、§4.2(缺 PIT 历史者仅实盘)。

## Global Constraints

- **离线可测**:纯规范化函数不 import akshare、不触网;测试用手搭的「原始 akshare 形状」DataFrame。
- **容错**:上游接口/网络失效 → 返回缺列/空,不抛(沿用现有 `_retry` + try 风格)。
- **无前视**:`net_inflow` 个股资金流仅近期历史(~100 日),历史缺失即缺列(热点因子按缺失中性处理);真实财报 PIT 不在本次范围。
- **不破坏**:`daily()` 现有列与语义不变,仅**增列** turnover/net_inflow(缺时不加)。
- **提交规范**:footer 两行;分支 `claude/superpower-brainstorming-grill-me-amuwan`。

---

### Task 1: `daily()` 增 turnover + net_inflow(纯规范化 + 薄包装)

**Files:** Modify `src/aselect/datasource/akshare_source.py`;Test `tests/test_akshare_normalize.py`(新建)

**Interfaces（模块级纯函数）:**
```python
_HIST_COLS 增 {"换手率": "turnover"}
def normalize_hist(raw: pd.DataFrame) -> pd.DataFrame
    # renames + 选列 + date 格式化；有换手率则带 turnover
def normalize_fund_flow(raw: pd.DataFrame) -> pd.DataFrame
    # {"日期":date, "主力净流入-净额":net_inflow} → DataFrame[date, net_inflow]
```
`daily()` = `normalize_hist(hist)` 后 best-effort merge `normalize_fund_flow(个股资金流)` 的 net_inflow(按 date 左连接;失败则跳过)。

- [ ] **Step 1** 写失败测试:`normalize_hist` 对含「换手率」的原始帧输出带 `turnover` 列、date 为 `YYYY-MM-DD`、只保留统一列;`normalize_fund_flow` 把「主力净流入-净额」→ `net_inflow`。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现纯函数 + 在 `daily()` 里用 `normalize_hist`,并 try 包 best-effort 资金流 merge。
- [ ] **Step 4** 跑测试通过(离线,不需 akshare)。
- [ ] **Step 5** 提交 `feat(datasource): akshare daily 增 turnover/net_inflow(纯规范化+容错)`。

---

### Task 2: `news(symbol)` 接 `stock_news_em`

**Files:** Modify `src/aselect/datasource/akshare_source.py`;Test `tests/test_akshare_normalize.py`

**Interfaces:**
```python
def normalize_news(raw: pd.DataFrame) -> list[dict]
    # {"新闻标题"/"新闻内容", "发布时间"} → [{"text":..., "date":"YYYY-MM-DD"}]
# AkshareSource.news(symbol): stock_news_em(symbol) → normalize_news；异常返回 []
```

- [ ] **Step 1** 写失败测试:`normalize_news` 把含「新闻标题/发布时间」的原始帧 → `[{text,date}]`,date 归一化到日;空帧 → []。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现 `normalize_news` + `AkshareSource.news`(容错返 []）。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 全量 `pytest -q` 绿;提交 `feat(datasource): akshare news(symbol) 接 stock_news_em`。

---

## Self-Review

**1. Spec coverage(§4.1/§4.2):** turnover(换手)、net_inflow(资金流)、news(舆情输入)真实字段;容错与缺失优雅退化;真实财报 PIT 明确列为范围外。
**2. Placeholder scan:** 纯函数有具体断言;`.ak` 薄包装无法离线测(已说明),靠纯函数覆盖解析逻辑。
**3. Type consistency:** `normalize_hist/-fund_flow/-news`、`news(symbol)->list[dict]{text,date}` 与 M1/M4 既有契约一致。

## 完成记录(2026-08-16)
- 2 任务 TDD 落地,全仓 155 测试通过,已推送(PR #1)。
- `daily()` 增 turnover(hist 自带)+ net_inflow(资金流 best-effort);`news(symbol)` 接 stock_news_em;解析逻辑抽为纯函数 `normalize_hist/-fund_flow/-news`,离线单测覆盖。
- ⚠️ **仅离线验证解析逻辑**:akshare 未装、沙箱无稳定网络,真实拉取**未联调**。真实环境需 `pip install akshare` + 放网,跑一次 `update`/`sync` 验证字段落库(见下)。

## 后续
- **真实环境联调(必做)**:装 akshare + 放网,`python -m aselect.cli sync` 验证 turnover/net_inflow/news 真实落库。
- 真实财报 PIT(`stock_financial_*` 按公告日)→ 让基本面因子在真实数据下也无前视。
- 龙虎榜事件标记(实盘信号)→ 需要时接 `stock_lhb_*`。
- 真实环境联调(装 akshare + 放网):跑一次 `update`/`sync` 验证字段落库。
