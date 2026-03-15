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

        # Use Celery if available, otherwise run synchronously for local dev
        use_sync = os.environ.get('RUN_SYNC', 'true').lower() == 'true'
        if use_sync:
            from apps.runs.tasks import execute_run
            execute_run(str(run.id))
        else:
            from apps.runs.tasks import execute_run
            execute_run.delay(str(run.id))

        from apps.runs.serializers import RunSerializer
        return Response(RunSerializer(run).data, status=status.HTTP_201_CREATED)


class AnalyzeProductView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AnalyzeProductRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Placeholder mock response
        return Response({
            'url': serializer.validated_data['url'],
            'name': 'Example Product',
            'description': 'An example product description extracted from the URL.',
            'category': 'SaaS',
        })


class DiscoverCompetitorsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DiscoverCompetitorsRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Placeholder mock response
        return Response({
            'competitors': [
                {'name': 'Competitor A', 'url': 'https://competitor-a.com', 'relevance': 0.95},
                {'name': 'Competitor B', 'url': 'https://competitor-b.com', 'relevance': 0.87},
                {'name': 'Competitor C', 'url': 'https://competitor-c.com', 'relevance': 0.78},
            ]
        })


class SuggestAreasView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SuggestAreasRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Placeholder mock response
        return Response({
            'areas': [
                {'key': 'onboarding', 'label': 'Onboarding Flow', 'description': 'First-time user experience'},
                {'key': 'pricing', 'label': 'Pricing Page', 'description': 'Pricing presentation and clarity'},
                {'key': 'navigation', 'label': 'Navigation', 'description': 'Site navigation and information architecture'},
                {'key': 'performance', 'label': 'Performance', 'description': 'Page load speed and responsiveness'},
            ]
        })
