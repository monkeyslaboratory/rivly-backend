from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

import os
from apps.accounts.models import TeamMember
from apps.runs.models import Run
from .models import Job, Competitor
from .serializers import (
    JobSerializer,
    CompetitorSerializer,
    AnalyzeProductRequestSerializer,
    DiscoverCompetitorsRequestSerializer,
    SuggestAreasRequestSerializer,
)


class JobListCreateView(generics.ListCreateAPIView):
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        team_ids = TeamMember.objects.filter(
            user=self.request.user
        ).values_list('team_id', flat=True)
        return Job.objects.filter(team_id__in=team_ids)

    def perform_create(self, serializer):
        membership = TeamMember.objects.filter(user=self.request.user).first()
        if membership is None:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'team': 'User does not belong to any team.'})
        serializer.save(team=membership.team)


class JobDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        team_ids = TeamMember.objects.filter(
            user=self.request.user
        ).values_list('team_id', flat=True)
        return Job.objects.filter(team_id__in=team_ids)


class CompetitorCreateView(generics.CreateAPIView):
    serializer_class = CompetitorSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        job_id = self.kwargs['pk']
        team_ids = TeamMember.objects.filter(user=self.request.user).values_list('team_id', flat=True)
        try:
            job = Job.objects.get(pk=job_id, team_id__in=team_ids)
        except Job.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound('Job not found.')
        serializer.save(job=job)


class JobTriggerRunView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        team_ids = TeamMember.objects.filter(user=request.user).values_list('team_id', flat=True)
        try:
            job = Job.objects.get(pk=pk, team_id__in=team_ids)
        except Job.DoesNotExist:
            return Response({'detail': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)

        run = Run.objects.create(job=job, triggered_by=request.user)

        # Run in background thread for local dev, Celery for production
        use_celery = os.environ.get('USE_CELERY', 'false').lower() == 'true'
        if use_celery:
            from apps.runs.tasks import execute_discovery
            execute_discovery.delay(str(run.id))
        else:
            from apps.runs.tasks import _run_discovery
            import threading
            thread = threading.Thread(target=_run_discovery, args=(str(run.id),))
            thread.daemon = True
            thread.start()

        from apps.runs.serializers import RunSerializer
        return Response(RunSerializer(run).data, status=status.HTTP_201_CREATED)


class AnalyzeProductView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AnalyzeProductRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from apps.jobs.services.product_analyzer import analyze_product_url
        result = analyze_product_url(serializer.validated_data['url'])
        return Response(result)


class DiscoverCompetitorsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DiscoverCompetitorsRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from apps.jobs.services.product_analyzer import analyze_product_url
        from apps.jobs.services.competitor_discovery import discover_competitors
        product_url = serializer.validated_data['product_url']
        product_meta = analyze_product_url(product_url)
        competitors = discover_competitors(product_meta)
        return Response(competitors)


class CheckAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        urls = request.data.get('urls', [])
        if not urls:
            return Response({'error': 'No URLs provided'}, status=status.HTTP_400_BAD_REQUEST)
        from apps.jobs.services.access_checker import check_urls_access
        results = check_urls_access(urls)
        return Response(results)


class SuggestAreasView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SuggestAreasRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Hardcoded — areas are predefined
        return Response({
            'areas': [
                {'key': 'onboarding', 'label': 'Onboarding Flow', 'description': 'First-time user experience'},
                {'key': 'pricing', 'label': 'Pricing Page', 'description': 'Pricing presentation and clarity'},
                {'key': 'navigation', 'label': 'Navigation', 'description': 'Site navigation and information architecture'},
                {'key': 'performance', 'label': 'Performance', 'description': 'Page load speed and responsiveness'},
            ]
        })
