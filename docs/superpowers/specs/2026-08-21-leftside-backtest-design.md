# 左侧交易回测线 — 设计文档

- 日期：2026-08-21
- 状态：已批准（用户 OK），待 grill-me 压力校验后实施
- 类型：architectural（新增一条独立的左侧交易回测线，与现有右侧 swing 并列）

## 1. 背景与目标

现有系统是**右侧摆动（swing）交易**：入场闸门（反追高 / 回踩 MA20 企稳）+ 离场纪律（吊灯止损 / 硬止损 / 跌破 MA10 / 2R 分批 / 最大持仓 20 日），事件驱动逐仓回测，计入 A 股摩擦、T+1、涨跌停、walk-forward、退市股池。

用户想另开一条**左侧交易**的回测线，验证核心命题：

> **"左侧交易胜率高、但期望为负（大亏吃掉小赚）"在 A 股到底成不成立。**

本线作为**研究验证**（Q1=C），效果好再考虑实盘，效果差就存档。**不急着上实盘。**

## 2. 关键设计决策（来自 brainstorming）

| 决策 | 选择 | 含义 |
|---|---|---|
| Q1 意图 | **C 研究+未来可能用** | 先回测验证，效果好再谈实盘 |
| Q2 入场 | **A 超卖均值回归** | 单笔买入，不搞分批加仓（分批会把入场与仓位管理两个变量搅在一起） |
| Q3 触发 | **A RSI<30** | RSI(14)<30 触发，超卖最经典定义 |
| Q4 股票池 | **C 两套都跑** | 主实验放开池 + 对照复用右侧筛选池 |

## 3. 架构原则：复用引擎，只换入场闸门

**不另写回测器。** 在同一个 swing 引擎（`swing_backtest.simulate_position`）上换一个"左侧入场闸门"，其余全部复用：

- 离场纪律 `evaluate_exit`（吊灯/硬止损/MA10/2R/最大持仓）——**一字不改**
- 成交逻辑 `_fillable_open`（T+1 开盘、涨跌停顺延）——**复用**
- 成本（佣金/印花税/过户/滑点）——**复用**
- walk-forward + PIT + 退市股池——**复用**

这样左右两线的对比才干净：**只差入场侧一个变量，其他全控住。**

## 4. 新增代码（唯一新增）

### 4.1 `strategy_rules.py`：新增 `gate_oversold_rsi()`

与现有 `gate_pullback_ma20()` 同款纯函数模式：

```python
@dataclass(frozen=True)
class OversoldParams:
    rsi_period: int = 14
    rsi_oversold: float = 30.0

def gate_oversold_rsi(bars, params=OversoldParams()) -> GateResult:
    # RSI(14) < 30 → passed=True；历史不足保守拒绝
    # 纯确定性，不读文件/网络/时钟
```

- 信号：RSI(14) < 30
- 单笔买入，T+1 开盘成交（复用 `simulate_position` 的 entry_idx 语义）
- 参数为常识固定初值，训练段只粗检验不精调

### 4.2 引擎/编排：接入左侧入场闸门

- 在 swing 编排层（runner）增加"左侧入场"路径：用 `gate_oversold_rsi` 产出入场信号日，其余流程与右侧完全一致。
- CLI 增加入口（如 `swing --side left --pool broad|filtered`），输出左右两线对照。

## 5. 两套股票池

- **Run B（主实验，pool=broad）**：全部非科创板（688 开头剔除），不加低波/ROE 筛选 → 回答"左侧整体赚不赚钱"。
- **Run A（对照，pool=filtered）**：复用右侧低波+ROE>10+PE<35 池 → 回答"同一批好票里，左侧 vs 右侧期望谁高"。

## 6. 评估指标（与右侧同一套 SwingReport）

验收指标：**期望值 / 盈亏比 / 最大回撤 / 夏普**。胜率仅参考（宪法铁律：不拿胜率当验收/优化目标）。

产出：左右两线对照表（期望值、盈亏比、胜率、最大回撤、夏普、交易数）。

## 7. 验证口径

- 与右侧**同段历史**、**同 walk-forward**、**PIT 对齐**。
- 退市股池含在内（防幸存者偏差）。
- 若 Run A 结果"左侧期望 ≤ 右侧"，则命题成立，作为研究结论存档；若反超，再考虑敏感性检查（如 RSI<25）与是否实盘化。

## 8. 明确不做（YAGNI）

- 不做分批加仓（Q2 已排除）
- 不做估值低位入场（Q2-C 排除，且系统已验证 ROE/PE 无 alpha）
- 不精调 RSI 阈值（常识初值，粗检验即可）
- 不接入实盘信号（研究线，效果好再说）

## 9. 风险与诚实预期

- 左侧本质更依赖"猜底"，与用户已确认的"不预测只应对"框架相悖，因此**本线定位研究，不承诺能实盘**。
- 超卖信号在低波绩优池里可能很少（Run A 信号稀疏），需在报告里标注交易数。
- 若样本外结果无效/方向不稳，按用户铁律**不强行上线**，存档即可。

## 10. 实现记录（2026-08-21 落地）

- **新增** `strategy_rules.OversoldParams` + `gate_oversold_rsi`（RSI<30 触发，纯函数，确定性核心）。
- **新增** `runner.run_leftside_backtest(pool='broad'|'filtered')` + `_leftside_symbols`（broad=非科创非ST含退市；filtered=None 沿用右侧因子池）。
- **新增** CLI `leftside` 子命令：Run B(放开池) + Run A(右侧同池) + 右侧原线对照。
- **Run B 篮子收敛**：broad 默认 `top_n=30`（每周最超卖前 30 只篮子），不取全市场每只超卖股——那会产出几十万笔交易、4GB 机器 OOM。
- **修复既有 bug** `data.pipeline.build_cross_section`：PIT 模式下 base 原本只保留"有已披露基本面的票"，基本面稀疏时截面塌缩成十几只、全宇宙被误丢。改为保留全部符号、基本面 left-merge 补 NaN。
- **性能优化** `build_cross_section`：新增可选 `frames` 参数，回测循环复用已加载的全量行情，避免每周对全宇宙重读 2.4GB daily 表。
- 窗口建议 `2024-01-01 ~ 2026-08-18`（PIT 基本面在近两年有效；更早时段 ROE/PE 覆盖不足，Run A 退化为纯价格因子选择）。
- 测试：`tests/test_strategy_rules.py` 新增 4 个 gate_oversold_rsi 用例；相关文件 24+ 用例全过。
