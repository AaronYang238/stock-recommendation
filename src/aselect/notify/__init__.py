"""通知层：配置驱动、无 LLM、缺 webhook 优雅回退 NullNotifier。"""
from .base import Notifier, NullNotifier
from .factory import build_notifier

__all__ = ["Notifier", "NullNotifier", "build_notifier"]
