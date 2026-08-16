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
