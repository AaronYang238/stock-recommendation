# M3:飞书通知层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增极简、配置驱动、无 LLM 的飞书通知层,推送三类消息(每日候选 / 持仓离场提醒 / 风险预警),缺 webhook 或未启用时回退 `NullNotifier`,确定性核心照跑。

**Architecture:** 新建 `aselect/notify/` 包:`Notifier` 抽象 + `NullNotifier` + `FeishuWebhookNotifier`(webhook POST,传输可注入以便测试);纯函数消息格式化 `messages.py`;工厂 `build_notifier(config)`。`config.py` 增 `NotifyConfig`(带默认值,不破坏现有构造)。webhook URL 从环境变量读(不落配置/代码)。对照 DSA 的 3119 行上帝类,本层约 150 行。

**Tech Stack:** Python 3.10+、标准库 `urllib`(不引入 requests)、pytest。

**Spec:** `docs/superpowers/specs/2026-08-16-aselect-swing-strategy-design.md` §6(热插拔/回退)、§8(通知层)、里程碑 M3。

## Global Constraints

- **输出外壳,非核心**:`notify/` 是输出层,**不得被 engine 引用**;engine 不 import notify。
- **无 LLM**:通知层纯格式化,严禁调用 LLM。
- **密钥安全**(铁律6):飞书 webhook URL 从环境变量读(`webhook_url_env`),**禁止落配置文件/代码**。
- **优雅回退**:未启用 / 缺 webhook → `NullNotifier`(no-op 返回 False),核心完整可跑。
- **确定性**:消息格式化为纯函数;网络 I/O 仅在 `FeishuWebhookNotifier` 内,测试用注入传输。
- **提交规范**:footer 两行;分支 `claude/superpower-brainstorming-grill-me-amuwan`。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/aselect/config.py` | 配置 | 改:加 `NotifyConfig` + `Config.notify`(默认值)+ 解析 |
| `src/aselect/notify/__init__.py` | 包导出 | 建 |
| `src/aselect/notify/base.py` | `Notifier` 抽象 + `NullNotifier` | 建 |
| `src/aselect/notify/messages.py` | 三类消息纯格式化 | 建 |
| `src/aselect/notify/feishu.py` | `FeishuWebhookNotifier`(传输可注入) | 建 |
| `src/aselect/notify/factory.py` | `build_notifier(config)` | 建 |
| `src/aselect/runner.py` | `latest_candidates` | 改 |
| `src/aselect/cli.py` | `notify` 子命令 | 改 |
| `config/config.example.yaml` | 示例 notify 段 | 改 |
| `tests/test_notify.py` | 通知层单测 | 建 |

---

### Task 1: `NotifyConfig` + 配置解析

**Files:** Modify `src/aselect/config.py`;Test `tests/test_notify.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class NotifyConfig:
    enabled: bool = False
    channel: str = "feishu"
    webhook_url_env: str = "FEISHU_WEBHOOK"
    timeout_s: int = 5
    @property
    def webhook_url(self) -> str | None:   # 只从环境变量读
        return os.environ.get(self.webhook_url_env) or None
# Config 增字段（带默认，不破坏现有 Config(...) 构造）：
#   notify: NotifyConfig = field(default_factory=NotifyConfig)
```

- [ ] **Step 1** 写失败测试:`load_config` 读到 `notify.enabled`;`NotifyConfig(webhook_url_env=...)` 的 `webhook_url` 随环境变量变化(用 `monkeypatch.setenv`);默认 `Config(app=..,datasource=..,storage=..,backtest=..,ai=AIConfig())` 仍可构造(notify 取默认)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现:加 `NotifyConfig`;`Config` 加 `notify: NotifyConfig = field(default_factory=NotifyConfig)`(置于 `ai` 后、`raw` 前);`load_config` 解析 `notify` 段。
- [ ] **Step 4** 跑测试通过(并跑既有 config/构造相关测试确认未破坏)。
- [ ] **Step 5** 提交 `feat(config): NotifyConfig（飞书 webhook 走环境变量）`。

---

### Task 2: `Notifier` 抽象 + `NullNotifier` + 工厂

**Files:** Create `src/aselect/notify/base.py`、`factory.py`、`__init__.py`;Test `tests/test_notify.py`

**Interfaces:**
```python
class Notifier(ABC):
    @abstractmethod
    def send(self, title: str, lines: list[str]) -> bool: ...
class NullNotifier(Notifier):
    def send(self, title, lines) -> bool: return False   # no-op
def build_notifier(config) -> Notifier:
    # notify.enabled 且 channel=='feishu' 且 webhook_url 存在 → FeishuWebhookNotifier；否则 NullNotifier
```

- [ ] **Step 1** 写失败测试:`build_notifier` 在未启用/缺 webhook 时返回 `NullNotifier`,其 `send` 返回 False;启用+设了 `FEISHU_WEBHOOK` 环境变量时返回 `FeishuWebhookNotifier`(类型断言)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现 base + 工厂(工厂内延迟 import feishu 避免无谓依赖)。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 提交 `feat(notify): Notifier 抽象 + NullNotifier + 工厂`。

---

### Task 3: 三类消息纯格式化 `messages.py`

**Files:** Create `src/aselect/notify/messages.py`;Test `tests/test_notify.py`

**Interfaces:**
```python
def format_candidates(rows: list[dict], date: str) -> tuple[str, list[str]]
    # rows: [{symbol,name,total_score,gate_passed,industry}]
def format_exit_alert(trade: dict) -> tuple[str, list[str]]
    # trade: {symbol,name,reason,ret,entry_date,exit_date}
def format_risk_warning(warnings: list[str]) -> tuple[str, list[str]]
    # 如 ["医药行业持仓 3 只，超集中度上限 2", "泰格 日内 +5% 追高"]
```
均为纯函数,返回 (标题, 行列表),不做 I/O。

- [ ] **Step 1** 写失败测试:三个格式化函数返回非空标题 + 行;候选含 symbol/score/闸门状态字样;离场含 reason 与收益;风险预警逐条列出。确定性(同输入同输出)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现三个纯函数(简洁文本/markdown 行)。
- [ ] **Step 4** 跑测试通过。
- [ ] **Step 5** 提交 `feat(notify): 三类消息格式化（候选/离场/风险）`。

---

### Task 4: `FeishuWebhookNotifier`(传输可注入)

**Files:** Create `src/aselect/notify/feishu.py`;Test `tests/test_notify.py`

**Interfaces:**
```python
class FeishuWebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, timeout_s: int = 5, post_fn=None):
        # post_fn(url, payload_bytes, timeout) -> (status:int, body:str)；默认用 urllib
    def send(self, title: str, lines: list[str]) -> bool:
        # 组飞书 text 消息 JSON，POST；2xx 返回 True，异常/非 2xx 返回 False（不抛）
```

- [ ] **Step 1** 写失败测试:注入假 `post_fn` 捕获 URL 与 payload;`send` 组的 JSON 含 `msg_type="text"` 且 body 含标题与各行;`post_fn` 返回 200 → True;返回 500 或抛异常 → False(不向上抛)。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现(默认 `post_fn` 用 `urllib.request`;异常吞掉返 False,记 log)。
- [ ] **Step 4** 跑测试 + `test_no_llm_in_core` 通过。
- [ ] **Step 5** 提交 `feat(notify): FeishuWebhookNotifier（webhook POST，传输可注入）`。

---

### Task 5: CLI `notify` + 候选采集 + 示例配置

**Files:** Modify `src/aselect/runner.py`、`src/aselect/cli.py`、`config/config.example.yaml`;Test `tests/test_notify.py`

**Interfaces:**
- `latest_candidates(store, config, top_n=10) -> list[dict]`:以最新 as_of 建截面→打分→入场闸门,返回 [{symbol,name,total_score,gate_passed,industry}]。
- CLI `notify`:`build_notifier(config)` → `latest_candidates` → `format_candidates` → `send`;未配置时走 NullNotifier(离线安全,打印「未配置飞书,跳过发送」)。

- [ ] **Step 1** 写失败测试:合成库上 `latest_candidates` 返回非空、每项含 `gate_passed` 布尔;用注入 `post_fn` 的 FeishuWebhookNotifier 跑一次候选推送,断言 payload 含至少一个候选 symbol。
- [ ] **Step 2** 跑测试确认失败。
- [ ] **Step 3** 实现 `latest_candidates`(复用 build_cross_section/score_factors/entry_gate/_ohlc_frames)+ CLI `notify` + example 配置 notify 段(enabled:false 默认)。
- [ ] **Step 4** 跑测试通过;`python -m aselect.cli seed && python -m aselect.cli notify` 手测(NullNotifier 打印跳过)。
- [ ] **Step 5** 全量 `pytest -q` 绿;提交 `feat(cli): notify 子命令 + 候选采集 + 示例配置`。

---

## Self-Review

**1. Spec coverage(§6/§8/M3):** 三类推送(Task 3)、飞书 webhook(Task 4)、配置驱动 + 环境变量密钥(Task 1)、NullNotifier 回退(Task 2)、端到端 CLI(Task 5)、约 150 行且零 DSA 依赖(全程)。
**2. Placeholder scan:** 各 Task Step 含具体断言与最小实现;无 TBD/TODO。
**3. Type consistency:** `Notifier.send(title, lines)`、`build_notifier(config)`、三个 `format_*`、`FeishuWebhookNotifier(webhook_url, timeout_s, post_fn)`、`latest_candidates(...)->list[dict]` 跨 Task 一致。

## M3 完成记录(2026-08-16)
- 5 个任务全部 TDD 落地,全仓 135 测试通过,已推送。
- 通知层 `aselect/notify/`:Notifier 抽象 + NullNotifier 回退 + FeishuWebhookNotifier(传输可注入)+ 三类消息格式化 + 工厂,约 150 行,零 DSA 依赖、无 LLM。
- `NotifyConfig`(webhook 走环境变量);CLI `notify` 手测:未配置→NullNotifier 本地打印候选(含闸门状态),实盘候选剔除已退市。
- 顺带修正:`latest_candidates` 排除 status=='D'(退市股属回测池,不进实盘推送)。

## 后续(不属 M3)
- 离场提醒/风险预警接入实盘每日流程(sync 后自动推送)→ 调度集成阶段。
- 多渠道(企业微信/Telegram)→ 需要时按同一 Notifier 抽象扩展。
