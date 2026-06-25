#!/usr/bin/env bash
# 生产启动（个人自用，Linux）：构建前端 → gunicorn 单端口托管 API + 前端静态(whitenoise)。
#   bash scripts/serve.sh            # 默认 0.0.0.0:9090
#   PORT=9091 WORKERS=3 bash scripts/serve.sh
# 与 start.sh(dev) 区别：DEBUG=0、走 gunicorn、前端用构建产物而非 vite dev。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# venv 不存在则自动创建（避免在系统 Python 上 pip 撞 PEP 668）
if [ ! -d .venv ]; then
  echo "创建虚拟环境 .venv ..."
  "${PYTHON:-python3}" -m venv .venv
fi
if [ -f .venv/bin/activate ]; then source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then source .venv/Scripts/activate
fi

echo "[1/3] 安装依赖（含 gunicorn/whitenoise）..."
pip install -q -r requirements.txt -r backend/requirements.txt
pip install -q -e .

echo "[2/3] 构建前端..."
( cd frontend && { [ -d node_modules ] || npm install; }; npm run build )

echo "[3/3] 启动 gunicorn ..."
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-$(python -c 'import secrets;print(secrets.token_urlsafe(50))')}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-*}"
PORT="${PORT:-9090}"        # 默认 9090（在常见放行端口段内；可用 PORT= 覆盖）
python backend/manage.py migrate --noinput >/dev/null 2>&1 || true
echo "==> http://0.0.0.0:${PORT}  (API + 前端单源；Ctrl+C 停止)"
cd backend
exec gunicorn aselect_api.wsgi:application -b "0.0.0.0:${PORT}" \
     -w "${WORKERS:-2}" --timeout 120
