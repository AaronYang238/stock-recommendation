#!/usr/bin/env bash
# 一键启动（Linux/macOS）：自动装依赖 → 建配置 → 灌数据 → 同起后端(:8000)+前端(:9090)。
# 用法：  bash scripts/start.sh            # 首次会建 venv 并安装依赖
#         bash scripts/start.sh --no-seed  # 跳过合成数据灌库（已有真实数据时）
# Ctrl+C 同时停止前后端。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 自动选择 python3 / python
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
  echo "[1/5] 创建虚拟环境 .venv ..."
  "$PYTHON" -m venv .venv
fi
# 兼容 Linux/macOS(bin) 与 Windows git-bash(Scripts)
# shellcheck disable=SC1091
if [ -f .venv/bin/activate ]; then source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then source .venv/Scripts/activate
fi
echo "[2/5] 安装/校验依赖（首次较慢，之后秒过）..."
pip install -q -r requirements.txt
pip install -q -r backend/requirements.txt
pip install -q -e .

# 2) 配置
if [ ! -f config/config.yaml ]; then
  cp config/config.example.yaml config/config.yaml
  echo "      已生成 config/config.yaml"
fi

# 3) 数据：本地库不存在则用离线合成数据灌库（不联网、可复现）
if [ "$DO_SEED" = "1" ] && [ ! -f data_store/aselect.sqlite ]; then
  echo "[3/5] 灌入离线合成数据（aselect.cli seed）..."
  python -m aselect.cli seed
else
  echo "[3/5] 已有数据库或 --no-seed，跳过 seed"
fi

# 4) 后端（仅监听本机；前端经 Vite 代理 /api → :8000）
echo "[4/5] 启动后端 Django :8000 ..."
python backend/manage.py migrate --noinput >/dev/null 2>&1 || true
python backend/manage.py runserver 127.0.0.1:8000 >/tmp/aselect-backend.log 2>&1 &
BACKEND_PID=$!

cleanup() { echo; echo "停止服务 ..."; kill "$BACKEND_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 30); do
  curl -s http://127.0.0.1:8000/api/meta/ >/dev/null 2>&1 && break
  sleep 1
done

# 5) 前端（vite.config 已设 host:true，可外部访问 :9090）
echo "[5/5] 启动前端 Vite :9090 ..."
cd frontend
[ -d node_modules ] || { echo "      首次安装前端依赖（npm install）..."; npm install; }
cat <<EOF

============================================
  前端:     http://localhost:9090
  后端 API: http://127.0.0.1:8000/api/meta/
  后端日志: /tmp/aselect-backend.log
  Ctrl+C 停止全部
============================================
EOF
npm run dev   # 前台运行；Ctrl+C 触发 cleanup 杀后端
