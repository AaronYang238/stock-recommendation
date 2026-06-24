# CLAUDE.md — 个人 A 股选股系统

> 本文件是项目「宪法」,每次会话开头都会读到。只记录「忘了就会做错」的高信号约束。
> 完整需求见 `个人选股App-需求规格说明书.md`;密钥见 `.env`(禁止提交)。

---

## 北极星目标(第一性原理)

**最大化【风险调整后、扣除全部交易成本、样本外】的真实收益率。**

- 优化目标是 **期望值 / 夏普 / IC**,**不是预测准确率**。
- 准确率/胜率是陷阱:胜率 70% 但盈亏比差照样亏钱;次日方向准确率 >70% 几乎必是数据泄漏。
- 任何以「提高胜率/准确率」为名的改动,先停下来对齐本目标。

---

## 不可违背的铁律(违反即视为 bug,不是风格问题)

1. **确定性防火墙**:技术指标、因子打分、条件筛选、回测,必须 100% 由确定性代码完成。**严禁在数值核心内调用任何 LLM**。AI 只接在文本输入端/语言输出端,产出先落地为结构化特征再入库。
2. **杜绝前视 / 数据泄漏**(头号收益杀手):
   - 财务数据按 **披露日(point-in-time)** 对齐,不是报告期。
   - 回测股票池 **必须含历史退市/ST 标的**(防幸存者偏差)。
   - 任何在 t 时刻用到 t 之后信息的写法,一律按 bug 处理。
3. **验证方法**:用 **walk-forward(滚动前推)**,禁用会打乱时序的普通 K 折。保留一个**只测一次**的样本外集,禁止反复在上面调参。
4. **评估指标**:报告 **IC、夏普、期望值、最大回撤、盈亏比**;**禁止把胜率/准确率作为优化或验收目标**。IC 稳定在 0.03~0.05 即为好因子,不要追求虚高数字。
5. **A 股交易摩擦**:回测必须计入 **佣金、印花税(卖出单边)、过户费、滑点**,并模拟 **T+1** 与 **涨跌停无法成交**。基准用沪深300 / 中证全指。
6. **AI 热插拔**:AI 提供商/模型/Key/各功能开关全部 **配置驱动**,改配置不改业务代码。`enabled=false` 或缺 Key 时回退 `NullAnalyzer`,**确定性核心必须完整可跑**。
7. **范围**:**仅 A 股(沪深京)**。市场相关逻辑仍以适配器封装,便于未来扩展。

---

## 因子开发规范

- 每个因子入库前必须:**去极值(Winsorize)+ 标准化(Z-score)+ 行业中性 + 市值中性**。
  - 跳过中性化 = 你以为在选低估值,其实在押小盘股,风格切换即崩。
- 新因子先**单独算 IC 验证**,再决定是否纳入多因子加权。
- 优先**简单模型 + 好特征**;低信噪比下,复杂模型(深度学习)是过拟合放大器,需额外论证。
- AI 舆情情绪作为**正交因子**纳入打分,**不得让它直接预测涨跌**。

---

## 技术栈(默认,均经适配器封装可换)

| 用途 | 选型 |
|---|---|
| 数据源 | akshare(主,免费) / baostock(免费,含财务+披露日,不被封) / tushare(备,积分) |
| 计算/指标 | pandas + pandas-ta(不可用时回退经测试的向量化实现) |
| 回测 | backtrader(不可用时回退向量化无前视简版) |
| 存储 | SQLite(Parquet 规划中) |
| 后端 | Django + DRF(REST 外壳，只读核心，不含业务 ORM) |
| 前端 | React + Vite + TypeScript(单页，端口 9090，/api 代理到 :8000) |
| 调度 | APScheduler / cron(收盘后自动 sync，已接入) |
| AI | 兼容 OpenAI/Anthropic 协议,适配器 + 工厂模式 |

---

## 项目结构(模块边界)

```
src/aselect/                 ← 确定性核心包(与表现层解耦)
  datasource/  数据源适配器(akshare / tushare / synthetic)
  storage/     存储抽象 + SQLite 实现
  data/        采集 · 清洗 · 截面因子表构建(含退市/ST/板块)
  engine/      指标 · 因子 · 筛选 · 回测   ← AI 禁区
  ai/          AIAnalyzer 接口 · 各提供商适配器 · NullAnalyzer
  cli.py       命令行入口(seed/update/screen/backtest)
backend/       Django + DRF：把核心封装成 REST API(只读，无业务 ORM)
frontend/      React + Vite + TypeScript 单页前端
config/config.yaml  运行与 AI 配置        .env  密钥(禁止提交)
```

核心包 `aselect` 内依赖方向 `datasource → data → engine`;`ai` 只在 data 输入端与
app/api 输出端被调用,**不得被 engine 引用**(有 `test_no_llm_in_core` 静态守护)。
`backend/` `frontend/` 只是外壳,**不得被核心反向依赖**。

---

## 常用命令

```bash
python -m aselect.cli seed              # 离线合成数据填库(不联网、可复现)
python -m aselect.cli update --limit N  # 收盘后增量拉取真实数据(重试+容错)
python -m aselect.cli sync              # 全量同步(列表→日线→基本面+行业→基准指数)
python -m aselect.scheduler             # 调度守护：交易日收盘后自动 sync
python -m aselect.cli screen            # 多因子打分 + 条件筛选
python -m aselect.cli backtest <code>   # 单只回测(MA 交叉)
python -m aselect.cli factor-ic         # 单因子 walk-forward IC 研究(纳入加权前先验)
python -m aselect.cli strategy --top 20 --freq M           # 股票池级 walk-forward 回测
python -m aselect.cli strategy --oos 0.7  # 样本外纪律:训练段拟合IC权重，样本外只测一次
python backend/manage.py runserver 8000 # 后端 API
cd frontend && npm run dev              # 前端(http://localhost:9090)
pytest                                  # 测试
```

---

## 提交前自检清单

- [ ] 是否在优化"收益/夏普/IC"而非"准确率"?
- [ ] 新代码有无前视:用了披露日吗?股票池含退市股吗?
- [ ] engine/ 内有没有偷偷调用 LLM?
- [ ] 回测计入摩擦、T+1、涨跌停了吗?
- [ ] AI 关闭/缺 Key 时,核心还能跑通吗?
- [ ] 新因子做了中性化并单独验过 IC 吗?

---

## 免责

本系统仅供个人研究,不构成投资建议。任何策略上实盘前须充分样本外验证并计入全部成本,盈亏自负。
