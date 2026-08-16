"""通知抽象与空实现（输出外壳，不被 engine 引用，无 LLM）。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, title: str, lines: list[str]) -> bool:
        """发送一条消息（标题 + 若干行）。成功返回 True，失败/no-op 返回 False。"""


class NullNotifier(Notifier):
    """未启用 / 缺 webhook 时的回退：什么都不做，核心照跑。"""

    def send(self, title: str, lines: list[str]) -> bool:  # noqa: ARG002
        return False
