# 个人选股 App（A 股）

本地运行、仅面向中国 A 股（沪深京）的选股决策支持系统。从全市场几千只股票中，依据可配置规则与因子筛选候选股、回测验证，并附带**可热插拔的 AI 分析**。**不下单、不做投资决策**，只做筛选、回测与解释。

> 详见 [需求规格说明书](个人选股App-需求规格说明书.md)。

## 两条铁律（架构约束）

1. **确定性防火墙** —— 指标 / 因子 / 筛选 / 回测 100% 由确定性代码完成，`engine` 包内**严禁调用任何 LLM**（由 `tests/test_no_llm_in_core.py` 静态校验）。AI 产出只能作为已落地的特征列被引擎读取。
2. **AI 热插拔** —— 提供商 / 模型 / Key / 单个功能均通过配置切换；禁用或 Key 缺失时自动降级到 `NullAnalyzer`，确定性核心**完整可用**。

## 架构（四层）

```
数据源层(akshare/tushare/合成) → 数据层(采集/清洗/存储) → 引擎层[确定性核心] → 应用层(Django API + React)
                                       ▲AI接入点①            ▲AI接入点②(仅边缘)     ▲AI接入点③
```

| 模块 | 职责 |
|---|---|
| `aselect.datasource` | 数据源适配器（主源失效回退备用/合成源） |
| `aselect.storage` | 存储抽象 + SQLite 实现 |
| `aselect.data` | 采集、清洗、截面因子表构建 |
| `aselect.engine` | **确定性核心**：indicators / factors / screener / backtest |
| `aselect.ai` | 热插拔 AI：`AIAnalyzer` 抽象 + 工厂 + `NullAnalyzer` 降级 + 各适配器 |
| `backend/` | Django + DRF，把上面核心封装成 REST API（不含业务 ORM，数据仍走 aselect.storage） |
| `frontend/` | React + Vite + TypeScript 单页前端，消费 `/api`，含板块/状态标注与 ⓘ 术语提示 |

> `aselect` 核心与表现层解耦：Django 只是 REST 外壳，React 只是视图，两条铁律仍由核心保证。

## 一键启动（推荐）

自动建 venv、装依赖、建配置、灌离线数据，并同时拉起后端(:8000)与前端(:9090)，`Ctrl+C` 一并停止：

```bash
bash scripts/start.sh            # Linux / macOS（首次会装依赖，稍慢）
#   pwsh scripts/start.ps1       # Windows / PowerShell
#   bash scripts/start.sh --no-seed   # 已灌真实数据时跳过合成数据
```

打开 `http://localhost:9090` 即可。需要真实数据再单独 `python -m aselect.cli update`（见下）。

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

## Web 界面（React + Django）

需要两个进程：后端 Django(:8000) 提供 API，前端 Vite(:9090) 提供页面并把 `/api` 代理到后端。

```bash
# 后端（终端 1）：复用上面的 venv，已 pip install -e .
pip install -r backend/requirements.txt        # django + djangorestframework
python backend/manage.py migrate                # 初始化 Django 框架自身的占位库
python backend/manage.py runserver 8000

# 前端（终端 2）：需 Node 18+
cd frontend
npm install
npm run dev                                     # 打开 http://localhost:9090
```

打开 `http://localhost:9090`：

- **个股查询**：输入任意股票代码 + 起止日期，查看指定区间内的 **K 线（蜡烛图，红涨绿跌）+ MA20/MA60**。
- **候选股**：表格带板块/状态分类与表头 ⓘ 术语提示，点选个股看 K 线、回测与 AI 报告。
- **策略回测**：股票池级 walk-forward 多因子回测，设持仓数/调仓频率，看策略净值 vs 基准曲线与 IC/超额/夏普/盈亏比等指标。

API 端点：`/api/meta`、`/api/candidates`、`/api/stocks/<code>/daily?start=&end=`（区间 K 线）、`/api/stocks/<code>/backtest|report`、`/api/strategy/backtest`。

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
- [x] 荐股板块标注：按代码前缀标注主板/创业板/科创板/北交所（CLI 与 Web 均显示，可按板块筛选）
- [x] Web 术语悬浮解释：专有名词右上角 ⓘ，悬停显示通俗描述（词典见 `aselect.glossary`）
- [x] 服务化：Django + DRF 后端 + React/Vite/TS 前端，替代 Streamlit（核心 `aselect` 不变）
- [x] 个股查询：按代码 + 起止日期查指定区间 K 线（蜡烛图 + MA20/MA60），`daily` 接口支持 `start/end`
- [x] 因子中性化：去极值 + Z-score + 行业/市值中性（OLS 残差），`total_score` 为中性化 Z 值
- [x] Point-in-time 防前视：基本面/特征带披露日 `ann_date`，截面按 `as_of` 只取已披露数据（`candidates` 接口支持 `as_of`）
- [x] 股票池级 walk-forward 多因子回测：含 A 股摩擦/T+1/涨跌停无法成交/基准对比，报告 IC·ICIR·夏普·盈亏比·期望值·超额；逐期 PIT 防前视、池含退市/ST（`strategy` 命令 + `/api/strategy/backtest`）
- [x] 真实财务/行业/披露日接入：akshare 行业（板块成分→symbol 映射）；**tushare 适配器补全**（`daily_basic` 估值 + `fina_indicator` 的 ROE/毛利率/同比 + 公告日 `ann_date`），为被封网环境提供可用数据路径与真实 PIT 财务
- [x] 数据自动化（整改阶段一）：`sync` 全量同步（列表→日线→基本面+行业→**真实沪深300基准入库**）；APScheduler 调度守护收盘后自动跑；tushare 适配器改批量+限频+退市；`/api/meta` 显示数据新鲜度
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
