"""通知器工厂：按配置构建；未启用/缺 webhook → NullNotifier（优雅回退）。"""
from __future__ import annotations

from .base import Notifier, NullNotifier


def build_notifier(config) -> Notifier:
    nc = getattr(config, "notify", None)
    if nc is None or not nc.enabled:
        return NullNotifier()
    if nc.channel == "feishu" and nc.webhook_url:
        from .feishu import FeishuWebhookNotifier
        return FeishuWebhookNotifier(nc.webhook_url, timeout_s=nc.timeout_s)
    return NullNotifier()
