"""飞书 webhook 通知（传输可注入以便测试；异常吞掉不影响主流程）。"""
from __future__ import annotations

import json
import logging
import urllib.request

from .base import Notifier

log = logging.getLogger(__name__)


def _urllib_post(url: str, payload: bytes, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.status, resp.read().decode("utf-8", "ignore")


class FeishuWebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, timeout_s: int = 5, post_fn=None):
        self.webhook_url = webhook_url
        self.timeout_s = timeout_s
        self._post = post_fn or _urllib_post

    def send(self, title: str, lines: list[str]) -> bool:
        text = title + "\n" + "\n".join(lines)
        payload = json.dumps({"msg_type": "text", "content": {"text": text}},
                             ensure_ascii=False).encode("utf-8")
        try:
            status, _ = self._post(self.webhook_url, payload, self.timeout_s)
            if 200 <= status < 300:
                return True
            log.warning("飞书推送非 2xx：%s", status)
            return False
        except Exception as e:  # noqa: BLE001  网络失败不得影响主流程
            log.warning("飞书推送失败：%s", e)
            return False
