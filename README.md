# 个人选股 App（A 股）

本地运行、仅面向中国 A 股（沪深京）的选股决策支持系统。从全市场几千只股票中，依据可配置规则与因子筛选候选股、回测验证，并附带**可热插拔的 AI 分析**。**不下单、不做投资决策**，只做筛选、回测与解释。

> 详见 [需求规格说明书](个人选股App-需求规格说明书.md)。

## 两条铁律（架构约束）

1. **确定性防火墙** —— 指标 / 因子 / 筛选 / 回测 100% 由确定性代码完成，`engine` 包内**严禁调用任何 LLM**（由 `tests/test_no_llm_in_core.py` 静态校验）。AI 产出只能作为已落地的特征列被引擎读取。
2. **AI 热插拔** —— 提供商 / 模型 / Key / 单个功能均通过配置切换；禁用或 Key 缺失时自动降级到 `NullAnalyzer`，确定性核心**完整可用**。

## 架构（四层）

```
数据源层(akshare/tushare/合成) → 数据层(采集/清洗/存储) → 引擎层[确定性核心] → 应用层(命令行 CLI + 通知)
                                       ▲AI接入点①            ▲AI接入点②(仅边缘)     ▲AI接入点③
```

| 模块 | 职责 |
|---|---|
| `aselect.datasource` | 数据源适配器（主源失效回退备用/合成源） |
| `aselect.storage` | 存储抽象 + SQLite 实现 |
| `aselect.data` | 采集、清洗、截面因子表构建 |
| `aselect.engine` | **确定性核心**：indicators / factors / screener / backtest |
| `aselect.ai` | 热插拔 AI：`AIAnalyzer` 抽象 + 工厂 + `NullAnalyzer` 降级 + 各适配器 |
| `aselect.notify` | 通知外壳：`Notifier` 抽象 + `FeishuWebhookNotifier` + `NullNotifier` 回退 |
| `aselect.cli` / `aselect.scheduler` | 命令行入口 + 收盘后自动 sync 调度守护 |

> 本项目为**纯命令行工具**（无 Web 前后端）：表现层即 CLI 与通知外壳，两条铁律仍由确定性核心保证。

## 一键启动（推荐）

自动建 venv、装依赖、建配置、灌离线合成数据，并打印常用 CLI 命令：

```bash
bash scripts/start.sh            # Linux / macOS（首次会装依赖，稍慢）
#   bash scripts/start.sh --no-seed   # 已灌真实数据时跳过合成数据
```

之后 `source .venv/bin/activate`，即可用 `python -m aselect.cli <命令>`（见下）。接真实数据用 `python -m aselect.cli update`。

## 快速开始（手动分步）

```bash
# 1. 建并激活虚拟环境（强烈建议；Debian/Ubuntu 的系统 Python 会拒绝直接装包）
python3 -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt          # 完整依赖
# 最小可跑（离线核心）：pip install pandas numpy pyyaml pyarrow pytest

# 3. 把本项目装为可编辑包（src-layout，否则 `python -m aselect.*` 会报 No module named 'aselect'）
pip install -e .                         # 或临时用：export PYTHONPATH=src

cp config/config.example.yaml config/config.yaml

python -m aselect.cli seed               # 用合成数据离线填充本地库（不联网、可复现）
python -m aselect.cli screen --top 10    # 多因子打分 + 条件筛选
python -m aselect.cli backtest 600519    # 单只回测（含 A 股交易摩擦）
```

接真实 A 股数据：`python -m aselect.cli update --limit 50`（默认 akshare，免费无 Key）。

> 若服务器出口 IP 被东方财富封锁（akshare 行情连接被 reset），有两条不被封的路：
> - **baostock（免费推荐）**：`pip install baostock`，把 `datasource.primary` 设为 `baostock`。
>   无需 token/积分，API 走自有服务、不被封，且**免费提供 ROE/毛利率/同比等真实财务 + 披露日**
>   （PIT 回测所需）与行业。
> - **tushare（付费积分）**：注册 → `export TUSHARE_TOKEN=...` → `primary: tushare`。
>   `daily_basic`/`fina_indicator` 等接口需较高积分（通常需年费赞助）。

## 命令一览

```bash
python -m aselect.cli seed                  # 离线合成数据填库（不联网、可复现）
python -m aselect.cli update --limit N      # 收盘后增量拉真实数据（重试+容错）
python -m aselect.cli sync                  # 全量同步（列表→日线→基本面+行业→基准指数）
python -m aselect.cli screen --top 10       # 多因子打分 + 条件筛选
python -m aselect.cli backtest 600519       # 单只回测（MA 交叉，含 A 股摩擦）
python -m aselect.cli factor-ic             # 单因子 walk-forward IC 研究
python -m aselect.cli strategy --oos 0.7    # 股票池级样本外回测（月度调仓）
python -m aselect.cli swing --top 10        # 事件驱动周级摆动回测（入场闸门+移动止损）
python -m aselect.cli swing --oos 0.7       # 摆动回测样本外一次性验收
python -m aselect.cli ablation              # 消融对照：追高/过早止盈两大风险量化成钱
python -m aselect.cli notify                # 飞书推送候选（未配置则本地打印）
python -m aselect.cli sentiment             # AI 舆情情绪 → 正交因子入库（AI 关则中性）
python -m aselect.scheduler                 # 调度守护：交易日收盘后自动 sync
```

## 常驻调度（个人自用）

用 systemd 守护调度进程，交易日收盘后自动 `sync`（拉数据 → 重算因子快照 → 生成当日推荐 → 回填历史战绩）：

```bash
# 编辑 deploy/aselect-scheduler.service 里的路径后：
sudo cp deploy/aselect-scheduler.service /etc/systemd/system/
sudo systemctl enable --now aselect-scheduler
```

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
- [x] 荐股板块标注：按代码前缀标注主板/创业板/科创板/北交所（可按板块筛选）
- [x] 纯命令行化：移除 Web 前后端（Django/DRF + React/Vite），表现层收敛到 `aselect.cli` / `aselect.scheduler`，确定性核心 `aselect` 不变
- [x] 因子中性化：去极值 + Z-score + 行业/市值中性（OLS 残差），`total_score` 为中性化 Z 值
- [x] Point-in-time 防前视：基本面/特征带披露日 `ann_date`，截面按 `as_of` 只取已披露数据
- [x] 股票池级 walk-forward 多因子回测：含 A 股摩擦/T+1/涨跌停无法成交/基准对比，报告 IC·ICIR·夏普·盈亏比·期望值·超额；逐期 PIT 防前视、池含退市/ST（`strategy` 命令）
- [x] 真实财务/行业/披露日接入：akshare 行业（板块成分→symbol 映射）；**tushare 适配器补全**（`daily_basic` 估值 + `fina_indicator` 的 ROE/毛利率/同比 + 公告日 `ann_date`），为被封网环境提供可用数据路径与真实 PIT 财务
- [x] 数据自动化（整改阶段一）：`sync` 全量同步（列表→日线→基本面+行业→**真实沪深300基准入库**）；APScheduler 调度守护收盘后自动跑；tushare 适配器改批量+限频+退市；`/api/meta` 显示数据新鲜度
- [x] 收益验证（整改阶段二）：单因子 walk-forward IC 研究（IC 均值/ICIR/IC胜率/分层多空/衰减，`factor-ic`）；按 IC 聚合类别权重；**样本外(hold-out)纪律**（`strategy --oos`：训练段拟合权重、样本外只测一次）
- [x] 推荐战绩：每日推荐落库 + **事后前向收益跟踪**（5/20 日，`recommendations` 表）；`factor_snapshot` 缓存；SQLite WAL
- [x] 中波段右侧策略子系统：热点因子、反追高入场闸门、反卖飞移动止损、事件驱动周级回测 + 消融对照、样本外验收、飞书通知、AI 舆情正交因子（`swing`/`ablation`/`notify`/`sentiment`；详见 `docs/superpowers/`）
- [x] 飞书通知推送（候选/离场/风险预警，配置驱动，缺 webhook 回退 NullNotifier）
- [ ] 监控预警推送（邮件/Telegram/企业微信）— 待接
- [ ] 6. NL 筛选与 AI 报告接入真实 Key 联调
- [ ] 接入点①：舆情/公告 情绪与事件因子（爬取/拉取财经文本 → AI 落地为因子）— 见下方「规划」，**暂不实现**
- [ ] 7.（可选）FastAPI 服务化 + 调度

## 规划：舆情/公告 接入点①（待实现，暂不开发）

为 AI 接入点①（`analyze_sentiment` / `extract_events`）补充文本数据源，把新闻/公告
转成结构化情绪与事件因子。链路在架构上已就绪（`features` 表已含
`sentiment / event_type / confidence / as_of / source` 列），当前为 `NullAnalyzer` 占位。

设计取舍（落地时遵循）：

- **优先级**：交易所公告/官方披露 > 研报 > 主流财经新闻 > 股吧。公告信号最干净、合规无争议；
  泛新闻噪声大、易滞后，仅作补充。
- **数据获取**：优先用 akshare 等现成新闻/公告接口（套现有 `DataSource` 适配器），
  避免自建爬虫带来的反爬、版权与 robots/ToS 合规风险；确需自建时先确认目标站点许可。
- **防前视污染（铁律一 / §6）**：每条 AI 特征必须带 `as_of`=信息真正公开的时间，
  回测严禁把"未来文本"喂给当时决策点。
- **成本控制（§4.6）**：只对候选池（几十只）跑 AI、带缓存、便宜模型初筛，禁止全市场逐条跑。
- **可复现**：原始文本与抽取结果均落库快照。
- **不必要性说明**：此项仅服务 AI 因子；确定性核心（筛选/因子/回测）在其缺失时仍完整可用（优雅降级）。

## 免责声明

本系统仅供研究，不构成投资建议；任何策略实盘前须充分回测并计入成本，盈亏自负。
