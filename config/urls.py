from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/auth/', include('apps.accounts.urls')),
    path('api/v1/jobs/', include('apps.jobs.urls')),
    path('api/v1/runs/', include('apps.runs.urls')),
    path('api/v1/health/', include('apps.health.urls')),
]
