"""飞书通知层：配置、抽象/回退、消息格式化、webhook、端到端（输出外壳，无 LLM）。"""
from __future__ import annotations

from aselect.config import AIConfig, Config, NotifyConfig, load_config


def _cfg(notify: NotifyConfig | None = None) -> Config:
    kw = dict(app={}, datasource={"adjust": "hfq"}, storage={},
              backtest={}, ai=AIConfig())
    if notify is not None:
        kw["notify"] = notify
    return Config(**kw)


# ── Task 1: NotifyConfig ────────────────────────────────────
def test_config_has_notify_default():
    cfg = _cfg()                       # 不传 notify 也能构造（默认值）
    assert cfg.notify.enabled is False
    assert cfg.notify.channel == "feishu"


def test_notify_webhook_url_from_env(monkeypatch):
    nc = NotifyConfig(enabled=True, webhook_url_env="MY_FEISHU_HOOK")
    assert nc.webhook_url is None
    monkeypatch.setenv("MY_FEISHU_HOOK", "https://open.feishu.cn/hook/abc")
    assert nc.webhook_url == "https://open.feishu.cn/hook/abc"


def test_load_config_parses_notify(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("notify:\n  enabled: true\n  webhook_url_env: FOO_HOOK\n",
                 encoding="utf-8")
    cfg = load_config(p)
    assert cfg.notify.enabled is True
    assert cfg.notify.webhook_url_env == "FOO_HOOK"


# ── Task 2: 抽象 + 回退 + 工厂 ──────────────────────────────
def test_factory_returns_null_when_disabled():
    from aselect.notify import NullNotifier, build_notifier
    n = build_notifier(_cfg())                      # 未启用
    assert isinstance(n, NullNotifier)
    assert n.send("t", ["a"]) is False              # no-op


def test_factory_returns_null_when_enabled_but_no_webhook():
    from aselect.notify import NullNotifier, build_notifier
    n = build_notifier(_cfg(NotifyConfig(enabled=True, webhook_url_env="NOPE_HOOK")))
    assert isinstance(n, NullNotifier)              # 缺 webhook → 回退


def test_factory_returns_feishu_when_configured(monkeypatch):
    from aselect.notify import build_notifier
    from aselect.notify.feishu import FeishuWebhookNotifier
    monkeypatch.setenv("OK_HOOK", "https://open.feishu.cn/hook/x")
    n = build_notifier(_cfg(NotifyConfig(enabled=True, webhook_url_env="OK_HOOK")))
    assert isinstance(n, FeishuWebhookNotifier)


# ── Task 3: 消息格式化 ──────────────────────────────────────
def test_format_candidates():
    from aselect.notify.messages import format_candidates
    rows = [{"symbol": "600276", "name": "恒瑞医药", "total_score": 1.23,
             "gate_passed": True, "industry": "医药"},
            {"symbol": "300347", "name": "泰格医药", "total_score": 0.98,
             "gate_passed": False, "industry": "医药"}]
    title, lines = format_candidates(rows, "2026-08-16")
    assert "2026-08-16" in title
    body = "\n".join(lines)
    assert "600276" in body and "恒瑞医药" in body
    assert any("闸门" in ln for ln in lines)


def test_format_exit_alert():
    from aselect.notify.messages import format_exit_alert
    title, lines = format_exit_alert({
        "symbol": "600886", "name": "国投电力", "reason": "trailing_stop",
        "ret": 0.084, "entry_date": "2026-08-01", "exit_date": "2026-08-12"})
    body = "\n".join(lines)
    assert "600886" in body and "trailing_stop" in body
    assert "8.4" in body or "8.40" in body       # 收益百分比


def test_format_risk_warning():
    from aselect.notify.messages import format_risk_warning
    title, lines = format_risk_warning(["医药持仓 3 只，超集中度上限 2",
                                        "泰格 日内 +5% 追高"])
    assert len(lines) == 2
    assert "医药" in lines[0]


def test_formatters_deterministic():
    from aselect.notify.messages import format_candidates
    rows = [{"symbol": "1", "name": "a", "total_score": 1.0,
             "gate_passed": True, "industry": "x"}]
    assert format_candidates(rows, "d") == format_candidates(rows, "d")
