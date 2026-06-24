from django.urls import path

from . import views

urlpatterns = [
    path("meta/", views.meta),
    path("candidates/", views.candidates),
    path("stocks/<str:symbol>/daily/", views.daily),
    path("stocks/<str:symbol>/backtest/", views.backtest),
    path("stocks/<str:symbol>/report/", views.report),
    path("strategy/backtest/", views.strategy_backtest),
    path("research/report/", views.research_report),
]
