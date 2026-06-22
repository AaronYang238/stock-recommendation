# 个人选股 App（A 股）

本地运行、仅面向中国 A 股（沪深京）的选股决策支持系统。从全市场几千只股票中，依据可配置规则与因子筛选候选股、回测验证，并附带**可热插拔的 AI 分析**。**不下单、不做投资决策**，只做筛选、回测与解释。

> 详见 [需求规格说明书](个人选股App-需求规格说明书.md)。

## 两条铁律（架构约束）

1. **确定性防火墙** —— 指标 / 因子 / 筛选 / 回测 100% 由确定性代码完成，`engine` 包内**严禁调用任何 LLM**（由 `tests/test_no_llm_in_core.py` 静态校验）。AI 产出只能作为已落地的特征列被引擎读取。
2. **AI 热插拔** —— 提供商 / 模型 / Key / 单个功能均通过配置切换；禁用或 Key 缺失时自动降级到 `NullAnalyzer`，确定性核心**完整可用**。

## 架构（四层）

```
数据源层(akshare/tushare/合成)  →  数据层(采集/清洗/存储)  →  引擎层[确定性核心]  →  应用层(Streamlit)
                                         ▲AI接入点①              ▲AI接入点②(仅边缘)      ▲AI接入点③
```

| 包 | 职责 |
|---|---|
| `aselect.datasource` | 数据源适配器（主源失效回退备用/合成源） |
| `aselect.storage` | 存储抽象 + SQLite 实现 |
| `aselect.data` | 采集、清洗、截面因子表构建 |
| `aselect.engine` | **确定性核心**：indicators / factors / screener / backtest |
| `aselect.ai` | 热插拔 AI：`AIAnalyzer` 抽象 + 工厂 + `NullAnalyzer` 降级 + 各适配器 |
| `aselect.app` | Streamlit 界面 |

## 快速开始

```bash
pip install -r requirements.txt          # 完整依赖
# 最小可跑（离线核心）：pip install pandas numpy pyyaml pyarrow pytest

cp config/config.example.yaml config/config.yaml

python -m aselect.cli seed               # 用合成数据离线填充本地库（不联网、可复现）
python -m aselect.cli screen --top 10    # 多因子打分 + 条件筛选
python -m aselect.cli backtest 600519    # 单只回测（含 A 股交易摩擦）

streamlit run src/aselect/app/streamlit_app.py   # 图形界面
```

接真实 A 股数据：`python -m aselect.cli update --limit 50`（默认 akshare，免费无 Key）。

## 启用 AI（可选，默认关闭）

在 `config/config.yaml` 设 `ai.enabled: true`、选 `provider`，并把密钥放进环境变量（**禁止写入仓库**）：

```bash
export AI_API_KEY=sk-...        # Windows PowerShell: $env:AI_API_KEY="sk-..."
```

每个接入点（sentiment / event_extraction / nl_to_filter / report_generation）可单独开关。本地模型把 `provider` 设为 `local` 并配 `base_url` 指向兼容 OpenAI 协议的服务即可。

## 测试

```bash
pytest          # 含：核心无 LLM 依赖、优雅降级、可复现、防注入、指标对拍
```

## 实施进度（对应需求第 7 节）

- [x] 1. MVP：数据 → SQLite → 条件筛选（PE/ROE/均线）+ Streamlit
- [x] 2. 指标与图表：MA/MACD/RSI/KDJ/BOLL + K 线
- [x] 3. 回测：backtrader（含成本/T+1），离线回退向量化无前视版
- [x] 4. AI 骨架：`AIAnalyzer` + 工厂 + 配置 + `NullAnalyzer` 降级（接入点①②③）
- [x] 5. 多因子打分排序（价值/成长/质量/动量/低波动）
- [x] 历史退市/ST 标的补全：合并沪/深退市接口 + 按名称识别 ST，股票池三态(L/ST/D)避免幸存者偏差
- [ ] 监控预警推送（邮件/Telegram/企业微信）— 待接
- [ ] 6. NL 筛选与 AI 报告接入真实 Key 联调
- [ ] 7.（可选）FastAPI 服务化 + 调度

## 免责声明

本系统仅供研究，不构成投资建议；任何策略实盘前须充分回测并计入成本，盈亏自负。
