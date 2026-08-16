# M1:数据源增强 + 热点因子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 aselect 增加「个股资金流/换手」数据字段与一个确定性、正交、单独 IC 可验的**热点因子**,契合用户的热点板块跟踪风格。

**Architecture:** 数据层给 `daily` 增两列(`turnover`、`net_inflow`),合成源确定性产出以便离线 TDD;`build_cross_section` 把最新值 + 日涨跌幅带入截面表;新增纯函数模块 `data/hotspot.py` 按行业聚合出板块相对强度/资金流/涨停广度并映射回个股;`engine/factors.py` 的 `FactorDef` 加 `industry_neutral` 开关,热点因子设 `False`(否则行业中性会把板块信息自我抵消),只做市值中性。全部为确定性核心,engine 无 LLM。

**Tech Stack:** Python 3.10+、pandas、numpy、SQLite、pytest。仅用仓库已有依赖,**不引入 daily_stock_analysis 依赖**。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md`(本计划实现其 §4 数据层、§5.1 热点因子、§11 里程碑 M1)

## Global Constraints

- **确定性防火墙**:本里程碑所有代码在 `datasource/`、`data/`、`engine/`,**严禁 import `aselect.ai`**;`tests/test_no_llm_in_core.py` 必须持续通过。
- **防前视**:热点因子只用 T 日盘后可得数据(资金流/换手/涨跌幅);任何缺 point-in-time 历史的字段不进因子(§4.2)。合成源产出的字段视为盘后已知。
- **因子规范**:入库前 winsorize → zscore → 中性化 → 再 zscore → 方向统一;热点因子**跳过行业中性、只市值中性**,再对已有因子正交由后续里程碑处理,本里程碑先单独 IC 验证。
- **评估口径**:验收看 **IC(稳定 0.03~0.05 为好)**,**不看胜率**。
- **提交规范**:每个 commit message 结尾附仓库要求的两行footer(`Co-Authored-By:` 与 `Claude-Session:`);分支 `claude/superpower-brainstorming-grill-me-amuwan`。
- **确定性**:相同输入恒得相同输出;合成源固定 seed。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/aselect/engine/factors.py` | 因子定义/处理/合成 | 改:`FactorDef` 加 `industry_neutral`;`score_factors` 按标志决定是否行业中性;注册 `hotspot` 因子 |
| `src/aselect/storage/sqlite_store.py` | SQLite schema | 改:`daily` 表加 `turnover`/`net_inflow` 列 + `_migrate` + `upsert_daily` 列表 |
| `src/aselect/datasource/synthetic_source.py` | 离线合成源 | 改:`daily()` 确定性产出 `turnover`/`net_inflow` |
| `src/aselect/data/clean.py` | 清洗 | 改:`clean_daily` 放行新列 |
| `src/aselect/data/pipeline.py` | 截面因子表 | 改:`build_cross_section` 带入 `net_inflow`/`turnover`/`pct_chg` |
| `src/aselect/data/hotspot.py` | 热点特征聚合(纯函数) | 建 |
| `tests/test_hotspot.py` | 热点因子单测 | 建 |
| `tests/test_factor_neutralization.py` | 中性化单测 | 改:加 industry_neutral 用例 |

---

### Task 1: `FactorDef.industry_neutral` 开关

**Files:**
- Modify: `src/aselect/engine/factors.py`
- Test: `tests/test_factor_neutralization.py`

**Interfaces:**
- Produces: `FactorDef(name, field, ascending, weight=1.0, industry_neutral=True)`;`score_factors(...)` 对 `industry_neutral=False` 的因子传 `industry=None`(仅市值中性)。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_factor_neutralization.py`:

```python
from aselect.engine.factors import FactorDef, process_factor, score_factors
import pandas as pd, numpy as np

def test_factordef_has_industry_neutral_flag_default_true():
    assert FactorDef("x", "x", ascending=False).industry_neutral is True
    assert FactorDef("h", "h", ascending=False, industry_neutral=False).industry_neutral is False

def test_score_factors_skips_industry_neutral_when_flag_false():
    # 构造:因子值 == 行业均值差异（纯行业信号）。行业中性会抹平它，跳过则保留。
    df = pd.DataFrame({
        "symbol": list("abcdef"),
        "industry": ["A", "A", "A", "B", "B", "B"],
        "total_mv": [1e9]*6,
        "hot": [1.0, 1.1, 0.9, 5.0, 5.1, 4.9],   # B 行业整体更高
    })
    factors = {"hotspot": [FactorDef("hot", "hot", ascending=False, industry_neutral=False)]}
    out = score_factors(df, factors=factors)
    # 跳过行业中性 → B 行业得分应显著高于 A 行业
    a = out.set_index("symbol").loc[["a","b","c"], "score_hotspot"].mean()
    b = out.set_index("symbol").loc[["d","e","f"], "score_hotspot"].mean()
    assert b - a > 1.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_factor_neutralization.py::test_score_factors_skips_industry_neutral_when_flag_false -v`
Expected: FAIL(`FactorDef` 无 `industry_neutral` 参数 / 行为未实现)

- [ ] **Step 3: 实现** — 改 `src/aselect/engine/factors.py`:

`FactorDef` 加字段:
```python
@dataclass(frozen=True)
class FactorDef:
    name: str
    field: str
    ascending: bool
    weight: float = 1.0
    industry_neutral: bool = True   # False=跳过行业中性(如热点因子,避免板块信息自我抵消)
```

`score_factors` 内层循环按标志选择 industry(原第 152 行附近):
```python
        for d in present:
            ind = industry if d.industry_neutral else None
            sub[d.name] = process_factor(out[d.field], d.ascending, ind, size) * d.weight
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_factor_neutralization.py -v`
Expected: PASS(含新旧用例)

- [ ] **Step 5: 提交**

```bash
git add src/aselect/engine/factors.py tests/test_factor_neutralization.py
git commit  # feat(engine): FactorDef.industry_neutral 开关，热点因子可跳过行业中性
```

---

### Task 2: 合成源与存储支持 `turnover` / `net_inflow`

**Files:**
- Modify: `src/aselect/datasource/synthetic_source.py`
- Modify: `src/aselect/storage/sqlite_store.py`
- Modify: `src/aselect/data/clean.py`
- Test: `tests/test_hotspot.py`(新建)

**Interfaces:**
- Produces: `SyntheticSource().daily(sym, adjust)` 输出含 `turnover`(换手率 %)、`net_inflow`(资金净流入,元)两列,确定性;`SQLiteStorage` `daily` 表持久化并可 `get_daily` 读回这两列。

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_hotspot.py`:

```python
import pandas as pd
from aselect.datasource.synthetic_source import SyntheticSource
from aselect.storage.sqlite_store import SQLiteStorage
from aselect.data.clean import clean_daily

def test_synthetic_daily_has_turnover_and_net_inflow():
    df = SyntheticSource().daily("600519", "hfq")
    assert {"turnover", "net_inflow"}.issubset(df.columns)
    assert df["turnover"].notna().all()

def test_synthetic_daily_is_deterministic_for_new_cols():
    a = SyntheticSource().daily("000001", "hfq")[["turnover", "net_inflow"]]
    b = SyntheticSource().daily("000001", "hfq")[["turnover", "net_inflow"]]
    pd.testing.assert_frame_equal(a, b)

def test_storage_roundtrips_new_daily_cols(tmp_path):
    store = SQLiteStorage(str(tmp_path / "t.sqlite"))
    raw = SyntheticSource().daily("600519", "hfq")
    store.upsert_daily("600519", clean_daily(raw), "hfq")
    back = store.get_daily("600519", "hfq")
    assert {"turnover", "net_inflow"}.issubset(back.columns)
    assert back["net_inflow"].notna().any()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hotspot.py -v`
Expected: FAIL(新列不存在)

- [ ] **Step 3: 实现合成源** — `synthetic_source.py` `daily()` 内,构造 `df` 前加(用同一 `rng`,保持确定性):

```python
        turnover = np.abs(rng.normal(3.0, 1.5, len(dates))).round(2)      # 换手率 %
        net_inflow = (rng.normal(0, 1, len(dates)) * volume * close * 0.01).round(0)  # 资金净流入(元)
```
并加入 `df` 字典:`"turnover": turnover, "net_inflow": net_inflow,`。

- [ ] **Step 4: 实现存储 schema** — `sqlite_store.py`:
  1. `_SCHEMA` 的 `daily` 表在 `volume REAL, amount REAL,` 后加 `turnover REAL, net_inflow REAL,`。
  2. `_migrate` 的 `wanted` 加:`"daily": [("turnover", "REAL"), ("net_inflow", "REAL")]`。
  3. `upsert_daily` 的 `cols` 列表尾部加 `"turnover", "net_inflow"`。

- [ ] **Step 5: 清洗放行** — `clean.py` `clean_daily` 不丢弃未知列(现有实现按 `_OHLC`/`volume` 过滤,新列会自然保留;加一条断言性注释即可,无逻辑改动)。确认 `_upsert` 已按表列过滤,安全。

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_hotspot.py -v`
Expected: PASS(前 3 个用例)

- [ ] **Step 7: 提交**

```bash
git add src/aselect/datasource/synthetic_source.py src/aselect/storage/sqlite_store.py src/aselect/data/clean.py tests/test_hotspot.py
git commit  # feat(data): daily 增 turnover/net_inflow 字段（合成源+存储+清洗）
```

---

### Task 3: 截面表带入 `net_inflow` / `turnover` / `pct_chg`

**Files:**
- Modify: `src/aselect/data/pipeline.py`
- Test: `tests/test_hotspot.py`

**Interfaces:**
- Consumes: `store.get_daily(sym, adjust, end=as_of)`(含新列)。
- Produces: `build_cross_section(...)` 返回的 DataFrame 每行含 `net_inflow`、`turnover`、`pct_chg`(最近一日涨跌幅,小数)三列;缺数据时为 NaN。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_hotspot.py`:

```python
from aselect.config import Config
from aselect.data.pipeline import update_symbols, update_daily, build_cross_section

def _seed_store(tmp_path):
    store = SQLiteStorage(str(tmp_path / "cs.sqlite"))
    ds = SyntheticSource()
    update_symbols(ds, store)
    syms = ds._all_symbols()
    update_daily(ds, store, syms, "hfq")
    store.upsert_fundamentals(ds.fundamentals(syms))
    return store, Config.default()

def test_cross_section_has_flow_and_pctchg(tmp_path):
    store, cfg = _seed_store(tmp_path)
    cross = build_cross_section(store, cfg)
    for col in ("net_inflow", "turnover", "pct_chg"):
        assert col in cross.columns
    assert cross["net_inflow"].notna().any()
```

> 注:若 `Config.default()` 不存在,改用测试内构造最小 Config(参照 `tests/test_factor_backtest.py` 的建法)。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hotspot.py::test_cross_section_has_flow_and_pctchg -v`
Expected: FAIL(列缺失)

- [ ] **Step 3: 实现** — `pipeline.py` `build_cross_section` 的 `for sym in base["symbol"]` 循环内,`if not daily.empty:` 块中补:

```python
            row["net_inflow"] = float(daily["net_inflow"].iloc[-1]) if "net_inflow" in daily else float("nan")
            row["turnover"] = float(daily["turnover"].iloc[-1]) if "turnover" in daily else float("nan")
            if len(daily) >= 2:
                row["pct_chg"] = float(daily["close"].iloc[-1] / daily["close"].iloc[-2] - 1)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_hotspot.py::test_cross_section_has_flow_and_pctchg -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/aselect/data/pipeline.py tests/test_hotspot.py
git commit  # feat(data): 截面表带入 net_inflow/turnover/pct_chg
```

---

### Task 4: 热点特征聚合器 `data/hotspot.py`

**Files:**
- Create: `src/aselect/data/hotspot.py`
- Test: `tests/test_hotspot.py`

**Interfaces:**
- Consumes: 截面 DataFrame(需含 `symbol`、`industry`、`mom_60`、`net_inflow`、`pct_chg`)。
- Produces: `add_hotspot_factor(cross: pd.DataFrame, limit_pct: float = 0.099) -> pd.DataFrame` 返回原表加一列 `hotspot`(原始值,越大=板块越热;缺失记 NaN),纯函数、确定性、不 import ai/store。

热点原始值定义(板块=industry):
- `sector_rel_strength[s]` = 板块内 `mom_60` 均值 − 全市场 `mom_60` 均值
- `sector_inflow[s]` = 板块内 `net_inflow` 之和,再对各板块做 zscore
- `sector_breadth[s]` = 板块内 `pct_chg >= limit_pct`(涨停)的成分股占比
- `hotspot_raw[i]` = `zscore_over_sectors(sector_rel_strength) + sector_inflow_z + zscore_over_sectors(sector_breadth)`,映射回该板块每只个股

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_hotspot.py`:

```python
from aselect.data.hotspot import add_hotspot_factor

def test_hotspot_ranks_hot_sector_above_cold():
    cross = pd.DataFrame({
        "symbol": list("abcdef"),
        "industry": ["医药","医药","医药","银行","银行","银行"],
        "mom_60":   [0.20, 0.22, 0.18, -0.02, 0.00, -0.01],   # 医药强
        "net_inflow":[5e7, 6e7, 4e7, -1e7, 0.0, -2e7],        # 医药资金流入
        "pct_chg":  [0.10, 0.05, 0.02, 0.00, 0.01, -0.01],    # 医药有涨停
    })
    out = add_hotspot_factor(cross)
    assert "hotspot" in out.columns
    hot = out.set_index("symbol").loc[["a","b","c"], "hotspot"].mean()
    cold = out.set_index("symbol").loc[["d","e","f"], "hotspot"].mean()
    assert hot > cold

def test_hotspot_is_deterministic():
    cross = pd.DataFrame({"symbol": ["a","b"], "industry": ["医药","银行"],
                          "mom_60": [0.2, -0.1], "net_inflow": [1e7, -1e7],
                          "pct_chg": [0.05, -0.02]})
    pd.testing.assert_frame_equal(add_hotspot_factor(cross), add_hotspot_factor(cross))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hotspot.py::test_hotspot_ranks_hot_sector_above_cold -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现** — 新建 `src/aselect/data/hotspot.py`:

```python
"""热点因子特征聚合（纯函数，确定性核心，AI 禁区）。

板块(=行业)层面聚合出相对强度/资金流/涨停广度，映射回个股为原始热点值。
热点本质是板块归属，故【不做行业中性】——由 engine.FactorDef(industry_neutral=False)
承接，只做市值中性，避免板块信息被自我抵消（见设计 spec §5.1）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if not std or std < 1e-10:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def add_hotspot_factor(cross: pd.DataFrame, limit_pct: float = 0.099) -> pd.DataFrame:
    out = cross.copy()
    need = {"industry", "mom_60", "net_inflow", "pct_chg"}
    if not need.issubset(out.columns):
        out["hotspot"] = np.nan
        return out

    overall_mom = out["mom_60"].mean()
    grp = out.groupby("industry")
    rel = grp["mom_60"].mean() - overall_mom               # 板块相对强度
    inflow = grp["net_inflow"].sum()                       # 板块资金净流入
    breadth = grp["pct_chg"].apply(lambda x: (x >= limit_pct).mean())  # 涨停广度

    sector_score = _zscore(rel) + _zscore(inflow) + _zscore(breadth)
    out["hotspot"] = out["industry"].map(sector_score)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_hotspot.py -v`
Expected: PASS(全部)

- [ ] **Step 5: 提交**

```bash
git add src/aselect/data/hotspot.py tests/test_hotspot.py
git commit  # feat(data): 热点因子板块聚合器 add_hotspot_factor
```

---

### Task 5: 注册热点因子 + 接入截面表 + 守护

**Files:**
- Modify: `src/aselect/engine/factors.py`(注册 `FactorDef`)
- Modify: `src/aselect/data/pipeline.py`(`build_cross_section` 末尾调用 `add_hotspot_factor`)
- Test: `tests/test_hotspot.py`、`tests/test_no_llm_in_core.py`

**Interfaces:**
- Consumes: `add_hotspot_factor`(Task 4)、`FactorDef.industry_neutral`(Task 1)。
- Produces: `DEFAULT_FACTORS["hotspot"] = [FactorDef("hotspot", "hotspot", ascending=False, industry_neutral=False)]`;`build_cross_section` 输出含 `hotspot` 列。

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_hotspot.py`:

```python
from aselect.engine.factors import score_factors, DEFAULT_FACTORS

def test_hotspot_registered_and_scored_end_to_end(tmp_path):
    store, cfg = _seed_store(tmp_path)
    cross = build_cross_section(store, cfg)
    assert "hotspot" in cross.columns
    assert "hotspot" in DEFAULT_FACTORS
    scored = score_factors(cross)
    assert "score_hotspot" in scored.columns
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hotspot.py::test_hotspot_registered_and_scored_end_to_end -v`
Expected: FAIL(`hotspot` 未注册/未接入)

- [ ] **Step 3: 实现**
  1. `factors.py` `DEFAULT_FACTORS` 加:
     ```python
         "hotspot": [
             FactorDef("hotspot", "hotspot", ascending=False, industry_neutral=False),
         ],
     ```
  2. `pipeline.py` `build_cross_section` `return cross` 前加:
     ```python
     from .hotspot import add_hotspot_factor
     cross = add_hotspot_factor(cross)
     ```
     (import 置于文件顶部与其它 `from .xxx` 同处)

- [ ] **Step 4: 跑测试确认通过 + 守护**

Run: `pytest tests/test_hotspot.py tests/test_no_llm_in_core.py -v`
Expected: PASS(端到端 + engine/data 无 LLM 守护仍通过)

- [ ] **Step 5: 提交**

```bash
git add src/aselect/engine/factors.py src/aselect/data/pipeline.py tests/test_hotspot.py
git commit  # feat: 注册热点因子并接入截面因子表
```

---

### Task 6: 热点因子单独 walk-forward IC 验证

**Files:**
- Modify: `src/aselect/runner.py`(若 `factor-ic` 编排在此;否则定位实际编排处)
- Test: `tests/test_hotspot.py`

**Interfaces:**
- Consumes: `engine.factor_research.summarize`、现有 `factor-ic` 编排。
- Produces: 热点因子出现在 `factor-ic` 报告中,返回 `FactorICReport(name="hotspot", ...)`,`ic_mean` 为有限数。

- [ ] **Step 1: 定位编排** — 先读 `src/aselect/runner.py` 与 `src/aselect/cli.py` 的 `factor-ic` 路径,确认单因子 IC 研究如何遍历因子、如何拿到各调仓日 `scores_by_date`。据此决定热点因子是否已随 `DEFAULT_FACTORS` 自动纳入(大概率是)。

- [ ] **Step 2: 写测试** — 追加到 `tests/test_hotspot.py`(用合成源多日面板;参照 `tests/test_factor_research.py` 的建法):

```python
from aselect.engine.factor_research import summarize

def test_hotspot_single_factor_ic_runs(tmp_path):
    store, cfg = _seed_store(tmp_path)
    # 复用工程内 factor-ic 编排取得 hotspot 的 scores_by_date/panel/schedule
    # （按 Step 1 定位到的函数调用；下方为形状断言）
    from aselect.runner import run_factor_research   # 若签名不同按实际调整
    reports = run_factor_research(store, cfg, freq="M")
    names = {r.name for r in reports}
    assert "hotspot" in names
    hot = next(r for r in reports if r.name == "hotspot")
    assert hot.ic_mean == hot.ic_mean   # 非 NaN（有限）
```

- [ ] **Step 3: 跑测试**

Run: `pytest tests/test_hotspot.py::test_hotspot_single_factor_ic_runs -v`
Expected: 先 FAIL(若 hotspot 未纳入 IC 遍历),据 Step 1 结论补齐编排使其纳入,再 PASS。

- [ ] **Step 4: 实现(仅在 Step 3 失败时)** — 在 factor-ic 编排的因子遍历里确保按 `DEFAULT_FACTORS` 全类别(含 hotspot)逐一算处理值与 IC。若编排已按 `DEFAULT_FACTORS` 动态遍历,则无需改代码,测试直接 PASS。

- [ ] **Step 5: 跑全量测试 + 提交**

Run: `pytest -q`
Expected: 全绿

```bash
git add -A
git commit  # test: 热点因子单独 walk-forward IC 验证纳入 factor-ic
```

---

## Self-Review

**1. Spec coverage(对 spec §4/§5.1/M1):**
- §4.1 新增字段:Task 2/3 落地 `turnover`/`net_inflow`(资金流)。龙虎榜/板块资金流按 §4.2 铁规则——龙虎榜为稀疏事件仅实盘,不进 M1 回测因子;板块资金流由成分股 `net_inflow` 聚合(Task 4),无需独立字段。✅
- §4.2 防前视:热点只用盘后可得字段;`build_cross_section` 已有 `as_of` PIT 过滤(沿用)。✅
- §5.1 热点因子构成(板块相对强度/资金流/涨停广度/板块内位置):Task 4 覆盖前三项;「个股在板块内相对位置」M1 未纳入,列为后续增强(不阻塞单因子 IC 验证)。⚠️ 已在下方「后续」记录。
- §5.1 中性化特例(跳过行业中性):Task 1 + Task 5(industry_neutral=False)。✅
- M1 验收(热点单独 IC):Task 6。✅

**2. Placeholder scan:** 无 TBD/TODO;各步含真实代码。Task 6 Step 1/4 依赖对 `runner.py`/`cli.py` 的现场核对(已显式要求先读文件,非占位)。

**3. Type consistency:** `add_hotspot_factor(cross, limit_pct=0.099) -> DataFrame`、`FactorDef(..., industry_neutral=True)`、新列名 `turnover`/`net_inflow`/`pct_chg`/`hotspot` 在各 Task 间一致。

## 后续(不属 M1,记录以免遗漏)
- ✅ **【已完成 2026-08-16】合成源历史季报快照**:`SyntheticSource.fundamentals()` 已改为逐季多期快照(真实 `ann_date`、严格 PIT)。解锁了基本面因子(roe n=34)与热点因子(hotspot n=32)的历史 IC(此前单快照下均为 0),`industry_neutral` 标志现可观测。见 `2026-08-16-m6-synthetic-fundamentals-history.md`。
- 「个股在板块内相对位置」子项 → 并入热点因子增强或 M2。
- 龙虎榜实盘信号(非回测因子) → M3 通知/实盘信号阶段。
- 热点因子对已有因子正交化 → 多因子加权阶段(M2)。

## M1 完成记录(2026-08-16)
- 6 个任务全部 TDD 落地,提交 `068ed63`(计划)→ `0bc771a`(Task 6);全仓 102 测试通过。
- 顺带修复 `run_factor_research` 无条件行业中性的真实 bug(现尊重 `industry_neutral`)。
- 已知局限见上「后续」首条(合成夹具,非热点逻辑缺陷)。

## Execution Handoff
见下方对话:选择 subagent-driven 或 inline 执行。
