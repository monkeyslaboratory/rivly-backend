import os
from pathlib import Path
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.http import FileResponse, Http404

from apps.accounts.models import TeamMember
from .models import Run, RunScreenshot
from .serializers import RunSerializer, RunListSerializer

SCREENSHOTS_DIR = Path(os.environ.get('SCREENSHOTS_DIR', 'media/screenshots'))


class RunListView(generics.ListAPIView):
    serializer_class = RunListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        team_ids = TeamMember.objects.filter(
            user=self.request.user
        ).values_list('team_id', flat=True)
        queryset = Run.objects.filter(job__team_id__in=team_ids)

        # Support both ?job_id= query param and /jobs/{id}/runs/ URL
        job_id = self.kwargs.get('job_id') or self.request.query_params.get('job_id')
        if job_id:
            queryset = queryset.filter(job_id=job_id)

        return queryset


class RunDetailView(generics.RetrieveAPIView):
    serializer_class = RunSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        team_ids = TeamMember.objects.filter(
            user=self.request.user
        ).values_list('team_id', flat=True)
        return Run.objects.prefetch_related(
            'screenshots', 'reports', 'overall_scores'
        ).filter(job__team_id__in=team_ids)


class ScreenshotView(APIView):
    """Serve screenshot PNG files by screenshot ID. Public access for image loading."""
    permission_classes = []  # Public — images are not sensitive
    authentication_classes = []

    def get(self, request, screenshot_id):
        try:
            shot = RunScreenshot.objects.get(id=screenshot_id)
        except RunScreenshot.DoesNotExist:
            raise Http404

        # Find local file by UUID in s3_key
        for f in SCREENSHOTS_DIR.glob('*.png'):
            if f.stem in shot.s3_key:
                response = FileResponse(open(f, 'rb'), content_type='image/png')
                response['Cache-Control'] = 'public, max-age=86400'
                return response

        raise Http404
