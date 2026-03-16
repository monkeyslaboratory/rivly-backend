from django.urls import path
from .views import RunListView, RunDetailView, ScreenshotView, RunApproveView, RunAddPagesView

urlpatterns = [
    path('', RunListView.as_view(), name='run-list'),
    path('jobs/<uuid:job_id>/runs/', RunListView.as_view(), name='run-list-by-job'),
    path('<uuid:pk>/', RunDetailView.as_view(), name='run-detail'),
    path('<uuid:pk>/approve/', RunApproveView.as_view(), name='run-approve'),
    path('<uuid:pk>/add-pages/', RunAddPagesView.as_view(), name='run-add-pages'),
    path('screenshots/<uuid:screenshot_id>/', ScreenshotView.as_view(), name='screenshot-serve'),
]
