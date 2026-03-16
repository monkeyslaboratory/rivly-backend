from django.urls import path
from .views import (
    JobListCreateView,
    JobDetailView,
    CompetitorCreateView,
    JobTriggerRunView,
    AnalyzeProductView,
    DiscoverCompetitorsView,
    SuggestAreasView,
    CheckAccessView,
)

urlpatterns = [
    path('', JobListCreateView.as_view(), name='job-list-create'),
    path('<uuid:pk>/', JobDetailView.as_view(), name='job-detail'),
    path('<uuid:pk>/competitors/', CompetitorCreateView.as_view(), name='competitor-create'),
    path('<uuid:pk>/run/', JobTriggerRunView.as_view(), name='job-trigger-run'),
    path('stepper/analyze-product/', AnalyzeProductView.as_view(), name='analyze-product'),
    path('stepper/discover-competitors/', DiscoverCompetitorsView.as_view(), name='discover-competitors'),
    path('stepper/suggest-areas/', SuggestAreasView.as_view(), name='suggest-areas'),
    path('stepper/check-access/', CheckAccessView.as_view(), name='check-access'),
]
