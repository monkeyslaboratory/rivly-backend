from django.urls import path
from .views import (
    JobListCreateView,
    JobDetailView,
    AnalyzeProductView,
    DiscoverCompetitorsView,
    SuggestAreasView,
)

urlpatterns = [
    path('', JobListCreateView.as_view(), name='job-list-create'),
    path('<uuid:pk>/', JobDetailView.as_view(), name='job-detail'),
    path('analyze-product/', AnalyzeProductView.as_view(), name='analyze-product'),
    path('discover-competitors/', DiscoverCompetitorsView.as_view(), name='discover-competitors'),
    path('suggest-areas/', SuggestAreasView.as_view(), name='suggest-areas'),
]
