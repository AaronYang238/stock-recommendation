"""飞书通知层：配置、抽象/回退、消息格式化、webhook、端到端（输出外壳，无 LLM）。"""
from __future__ import annotations

from aselect.config import AIConfig, Config, NotifyConfig, load_config


def _seed_store(tmp_path):
    from aselect.data.pipeline import update_daily, update_symbols
    from aselect.datasource.synthetic_source import SyntheticSource
    from aselect.storage.sqlite_store import SQLiteStorage
    store = SQLiteStorage(str(tmp_path / "n.sqlite"))
    ds = SyntheticSource(days=220)
    update_symbols(ds, store)
    syms = ds._all_symbols()
    update_daily(ds, store, syms, "hfq")
    store.upsert_fundamentals(ds.fundamentals(syms))
    return store


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


# ── Task 4: FeishuWebhookNotifier ───────────────────────────
def test_feishu_send_posts_expected_payload():
    import json

    from aselect.notify.feishu import FeishuWebhookNotifier
    captured = {}

    def fake_post(url, payload, timeout):
        captured["url"] = url
        captured["body"] = json.loads(payload.decode("utf-8"))
        return 200, "ok"

    n = FeishuWebhookNotifier("https://hook/x", post_fn=fake_post)
    assert n.send("标题", ["行1", "行2"]) is True
    assert captured["url"] == "https://hook/x"
    assert captured["body"]["msg_type"] == "text"
    text = captured["body"]["content"]["text"]
    assert "标题" in text and "行1" in text and "行2" in text


def test_feishu_send_returns_false_on_error():
    from aselect.notify.feishu import FeishuWebhookNotifier

    def bad_500(url, payload, timeout):
        return 500, "err"

    def raises(url, payload, timeout):
        raise OSError("network down")

    assert FeishuWebhookNotifier("u", post_fn=bad_500).send("t", ["a"]) is False
    assert FeishuWebhookNotifier("u", post_fn=raises).send("t", ["a"]) is False


# ── Task 5: 候选采集 + 端到端推送 ───────────────────────────
def test_latest_candidates(tmp_path):
    from aselect.runner import latest_candidates
    store = _seed_store(tmp_path)
    rows = latest_candidates(store, _cfg(), top_n=5)
    assert len(rows) > 0
    for r in rows:
        assert "symbol" in r and isinstance(r["gate_passed"], bool)


def test_candidates_push_end_to_end(tmp_path):
    import json

    from aselect.notify.feishu import FeishuWebhookNotifier
    from aselect.notify.messages import format_candidates
    from aselect.runner import latest_candidates
    store = _seed_store(tmp_path)
    rows = latest_candidates(store, _cfg(), top_n=5)
    captured = {}

    def fake_post(url, payload, timeout):
        captured["body"] = json.loads(payload.decode("utf-8"))
        return 200, "ok"

    title, lines = format_candidates(rows, "2026-08-16")
    ok = FeishuWebhookNotifier("u", post_fn=fake_post).send(title, lines)
    assert ok is True
    text = captured["body"]["content"]["text"]
    assert any(r["symbol"] in text for r in rows)
