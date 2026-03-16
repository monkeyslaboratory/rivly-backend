from django.urls import path
from .views import RunListView, RunDetailView, ScreenshotView

urlpatterns = [
    path('', RunListView.as_view(), name='run-list'),
    path('jobs/<uuid:job_id>/runs/', RunListView.as_view(), name='run-list-by-job'),
    path('<uuid:pk>/', RunDetailView.as_view(), name='run-detail'),
    path('screenshots/<uuid:screenshot_id>/', ScreenshotView.as_view(), name='screenshot-serve'),
]
