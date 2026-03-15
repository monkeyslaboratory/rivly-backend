from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.accounts.models import TeamMember
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
        serializer.save()


class JobDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        team_ids = TeamMember.objects.filter(
            user=self.request.user
        ).values_list('team_id', flat=True)
        return Job.objects.filter(team_id__in=team_ids)


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
