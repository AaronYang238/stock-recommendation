"""数据源工厂：按配置返回主源，并在主源不可用时回退备用源。"""
from __future__ import annotations

import logging

from ..config import Config
from .base import DataSource

log = logging.getLogger(__name__)


def _build(name: str, config: Config) -> DataSource:
    ds_cfg = config.datasource
    retry = int(ds_cfg.get("retry", 3))
    backoff = float(ds_cfg.get("retry_backoff_s", 2))
    if name == "akshare":
        from .akshare_source import AkshareSource
        return AkshareSource(retry=retry, retry_backoff_s=backoff)
    if name == "tushare":
        from .tushare_source import TushareSource
        return TushareSource(retry=retry, retry_backoff_s=backoff)
    if name == "synthetic":
        from .synthetic_source import SyntheticSource
        return SyntheticSource()
    raise NotImplementedError(f"未知数据源: {name}")


def get_datasource(config: Config) -> DataSource:
    """优先 primary；构造失败（如未装库）则回退 fallback。"""
    primary = config.datasource.get("primary", "akshare")
    fallback = config.datasource.get("fallback")
    try:
        return _build(primary, config)
    except Exception as e:  # noqa: BLE001
        log.warning("主数据源 %s 不可用(%s)，尝试回退 %s", primary, e, fallback)
        if fallback:
            try:
                return _build(fallback, config)
            except Exception as e2:  # noqa: BLE001
                log.warning("备用源 %s 亦不可用(%s)，回退合成源", fallback, e2)
        return _build("synthetic", config)
