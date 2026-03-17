import os
import threading
from pathlib import Path
from urllib.parse import urlparse
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
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


class RunApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        # Validate user access
        team_ids = TeamMember.objects.filter(user=request.user).values_list('team_id', flat=True)
        try:
            run = Run.objects.get(pk=pk, job__team_id__in=team_ids, status='discovered')
        except Run.DoesNotExist:
            return Response({'detail': 'Run not found or not in discovered state.'}, status=404)

        # Optional: remove screenshots the user deselected
        remove_ids = request.data.get('remove_screenshot_ids', [])
        if remove_ids:
            RunScreenshot.objects.filter(run=run, id__in=remove_ids).delete()

        # Start analysis phase
        from apps.runs.tasks import execute_analysis
        thread = threading.Thread(target=execute_analysis, args=(str(run.id),))
        thread.daemon = True
        thread.start()

        return Response({'status': 'approved', 'run_id': str(run.id)})


class RunAddPagesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        team_ids = TeamMember.objects.filter(user=request.user).values_list('team_id', flat=True)
        try:
            run = Run.objects.get(pk=pk, job__team_id__in=team_ids, status='discovered')
        except Run.DoesNotExist:
            return Response({'detail': 'Run not found.'}, status=404)

        urls = request.data.get('urls', [])
        if not urls:
            return Response({'detail': 'No URLs provided.'}, status=400)

        # Screenshot each new URL
        from apps.runs.services.screenshot import _capture_page
        from playwright.sync_api import sync_playwright

        new_shots = []
        competitor = run.job.competitors.first()
        if not competitor:
            return Response({'detail': 'No competitor found.'}, status=400)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            for url in urls:
                full_url = url if url.startswith('http') else f'https://{url}'
                name = urlparse(full_url).path.strip('/').split('/')[0] or 'custom'
                shot = _capture_page(page, full_url, f'custom_{name}', 'desktop', run, competitor, {'width': 1440, 'height': 900})
                new_shots.append({'id': str(shot.id), 'page_name': shot.page_name, 'status': shot.status})
            browser.close()

        return Response({'added': new_shots})


class RunAuthCrawlView(APIView):
    """Submit credentials and re-crawl auth-required pages."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        team_ids = TeamMember.objects.filter(user=request.user).values_list('team_id', flat=True)
        try:
            run = Run.objects.get(pk=pk, job__team_id__in=team_ids, status='discovered')
        except Run.DoesNotExist:
            return Response({'detail': 'Run not found.'}, status=404)

        credentials = request.data.get('credentials', {})
        if not credentials.get('email') or not credentials.get('password'):
            return Response({'detail': 'Email and password required.'}, status=400)

        # Store credentials (would be encrypted/vault in production)
        run.auth_credentials = credentials
        run.save(update_fields=['auth_credentials'])

        # Trigger authenticated crawl in background
        from apps.runs.services.screenshot import authenticated_crawl
        thread = threading.Thread(target=authenticated_crawl, args=(str(run.id),))
        thread.daemon = True
        thread.start()

        return Response({'status': 'auth_crawl_started'})


class RunSubmitCodeView(APIView):
    """Submit captcha text or 2FA code to continue auth."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        team_ids = TeamMember.objects.filter(user=request.user).values_list('team_id', flat=True)
        try:
            run = Run.objects.get(pk=pk, job__team_id__in=team_ids)
        except Run.DoesNotExist:
            return Response({'detail': 'Run not found.'}, status=404)

        code = request.data.get('code', '')
        if not code:
            return Response({'detail': 'Code required.'}, status=400)

        # Store code and re-trigger auth
        creds = run.auth_credentials or {}
        creds['verification_code'] = code
        run.auth_credentials = creds
        run.save(update_fields=['auth_credentials'])

        # Re-trigger auth crawl (it will use the stored code)
        from apps.runs.services.screenshot import submit_verification_code
        thread = threading.Thread(target=submit_verification_code, args=(str(run.id),))
        thread.daemon = True
        thread.start()

        return Response({'status': 'code_submitted'})
