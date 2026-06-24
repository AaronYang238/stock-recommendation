"""APScheduler 守护：每个交易日收盘后自动跑 sync（数据自动更新）。

  python -m aselect.scheduler          # 常驻；或用 cli `schedule`
配置（config.yaml 的 scheduler 段，可选）：
  scheduler: { hour: 16, minute: 30, limit: 0 }   # 周一~五 16:30 全量同步

注：A 股节假日不交易，cron 仍会触发但 sync 是增量+幂等，空跑无副作用。
"""
from __future__ import annotations

import argparse
import logging

from .config import load_config

log = logging.getLogger(__name__)


def run_sync_once(limit: int = 0) -> None:
    """直接复用 cli._sync（同进程调用，无子进程）。"""
    from .cli import _sync
    _sync(argparse.Namespace(limit=limit))


def build_scheduler(hour: int = 16, minute: int = 30, limit: int = 0):
    """构造（不启动）后台调度器，便于测试 / 嵌入。周一~五定时跑 sync。"""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BackgroundScheduler()
    sched.add_job(
        run_sync_once, CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        kwargs={"limit": limit}, id="daily_sync", replace_existing=True,
    )
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    sc = cfg.raw.get("scheduler", {}) or {}
    hour, minute = int(sc.get("hour", 16)), int(sc.get("minute", 30))
    limit = int(sc.get("limit", 0))

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler()
    sched.add_job(run_sync_once, CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
                  kwargs={"limit": limit}, id="daily_sync")
    log.info("调度启动：每周一~五 %02d:%02d 跑 sync（Ctrl+C 退出）", hour, minute)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度已停止")


if __name__ == "__main__":
    main()
