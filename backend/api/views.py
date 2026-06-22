"""DRF 视图：薄薄一层，参数解析 + 调 services，不含业务计算。"""
from __future__ import annotations

from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import services


def _to_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _csv(v):
    return [s for s in (v or "").split(",") if s] or None


@api_view(["GET"])
def meta(request):
    return Response(services.meta())


@api_view(["GET"])
def candidates(request):
    q = request.query_params
    data = services.candidates(
        pe_max=_to_float(q.get("pe_max"), 30),
        roe_min=_to_float(q.get("roe_min"), 10),
        top=_to_int(q.get("top"), 20),
        boards=_csv(q.get("boards")),
        statuses=_csv(q.get("statuses")),
    )
    return Response(data)


@api_view(["GET"])
def daily(request, symbol: str):
    return Response(services.daily_series(symbol))


@api_view(["GET"])
def backtest(request, symbol: str):
    q = request.query_params
    return Response(services.backtest(
        symbol, fast=_to_int(q.get("fast"), 5), slow=_to_int(q.get("slow"), 20)))


@api_view(["POST", "GET"])
def report(request, symbol: str):
    return Response(services.ai_report(symbol))
