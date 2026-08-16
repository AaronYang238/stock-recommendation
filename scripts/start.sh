#!/usr/bin/env bash
# 一键启动（纯命令行）：建 venv → 装依赖 → 建配置 → 灌离线数据 → 打印 CLI 用法。
# 本项目为纯命令行工具，无 Web 前后端。
#   bash scripts/start.sh            # 首次会建 .venv 并安装依赖 + 灌合成数据
#   bash scripts/start.sh --no-seed  # 已有真实数据时跳过合成数据灌库
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
fi
DO_SEED=1
for arg in "$@"; do
  [ "$arg" = "--no-seed" ] && DO_SEED=0
done

# 1) 虚拟环境 + 依赖
if [ ! -d .venv ]; then
  echo "[1/4] 创建虚拟环境 .venv ..."
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
if [ -f .venv/bin/activate ]; then source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then source .venv/Scripts/activate
fi
echo "[2/4] 安装/校验依赖（首次较慢，之后秒过）..."
pip install -q -r requirements.txt
pip install -q -e .

# 2) 配置
if [ ! -f config/config.yaml ]; then
  cp config/config.example.yaml config/config.yaml
  echo "      已生成 config/config.yaml"
fi

# 3) 数据：本地库不存在则用离线合成数据灌库（不联网、可复现）
if [ "$DO_SEED" = "1" ] && [ ! -f data_store/aselect.sqlite ]; then
  echo "[3/4] 灌入离线合成数据（aselect.cli seed）..."
  python -m aselect.cli seed
else
  echo "[3/4] 已有数据库或 --no-seed，跳过 seed"
fi

# 4) 用法提示
cat <<'EOF'
[4/4] 就绪。常用命令（先 `source .venv/bin/activate`）：

  python -m aselect.cli update --limit 50     # 收盘后增量拉真实数据
  python -m aselect.cli sync                  # 全量同步
  python -m aselect.cli screen                # 多因子打分 + 条件筛选
  python -m aselect.cli factor-ic             # 单因子 walk-forward IC 研究
  python -m aselect.cli strategy --oos 0.7    # 股票池级样本外回测
  python -m aselect.cli swing --top 10        # 事件驱动周级摆动回测
  python -m aselect.cli swing --oos 0.7       # 摆动回测样本外验收
  python -m aselect.cli ablation              # 两大风险消融对照
  python -m aselect.cli notify                # 飞书推送候选（需配置）
  python -m aselect.cli sentiment             # AI 舆情情绪因子入库（需启用 AI）
  python -m aselect.scheduler                 # 调度守护：交易日收盘后自动 sync
EOF
