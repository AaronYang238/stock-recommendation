"""基金推荐子系统（v1）。

模块：datasource（akshare 拉取+重试）、strategy（净值动量信号，确定性）、
backtest（月频回测含真实申赎费率）。与股票侧共享 storage/engine.stats/runner
基础设施；engine/ 内禁 LLM 的静态守护同样约束本包内进回测的数值核心。
"""
