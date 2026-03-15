from django.urls import path
from .views import RunListView, RunDetailView

urlpatterns = [
    path('', RunListView.as_view(), name='run-list'),
    path('<uuid:pk>/', RunDetailView.as_view(), name='run-detail'),
]
