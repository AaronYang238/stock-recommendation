"""Django 设置（仅作为 aselect 确定性核心的 REST 外壳，不含业务 ORM 模型）。

数据仍由 aselect.storage(SQLite/Parquet) 管理；Django 自身只需一个占位库满足框架要求。
开发态：DEBUG=True，前端经 Vite 代理 /api → :8000，故无需 CORS。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
REPO_ROOT = BASE_DIR.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:                            # 引入确定性核心包
    sys.path.insert(0, str(SRC))

# 开发用密钥；生产请用环境变量覆盖
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
# 生产用 DJANGO_ALLOWED_HOSTS 逗号分隔指定；DEBUG 下放开
ALLOWED_HOSTS = (os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
                 if os.environ.get("DJANGO_ALLOWED_HOSTS") else ["*"])

# 生产托管前端构建产物（whitenoise 装了且 frontend/dist 存在时启用，单源部署）
_DIST = REPO_ROOT / "frontend" / "dist"
SERVE_FRONTEND = False
try:
    import whitenoise  # noqa: F401
    SERVE_FRONTEND = _DIST.is_dir()
except ImportError:
    pass

INSTALLED_APPS = [
    "django.contrib.contenttypes",   # DRF 依赖
    "django.contrib.auth",           # DRF 依赖
    "django.contrib.staticfiles",
    "rest_framework",
    "api",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]
if SERVE_FRONTEND:
    # whitenoise 直接把 frontend/dist 挂在根路径（index.html + /assets/*）
    MIDDLEWARE.insert(0, "whitenoise.middleware.WhiteNoiseMiddleware")
    WHITENOISE_ROOT = str(_DIST)
    WHITENOISE_INDEX_FILE = True

ROOT_URLCONF = "aselect_api.urls"
TEMPLATES = []
WSGI_APPLICATION = "aselect_api.wsgi.application"

# Django 框架占位库（无业务模型；股票数据走 aselect.storage）
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "django.sqlite3",
    }
}

# 纯 JSON API：不挂任何认证/Session，避免 CSRF 干扰前端调用
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
