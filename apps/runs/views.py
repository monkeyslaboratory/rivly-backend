from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from apps.accounts.models import TeamMember
from .models import Run
from .serializers import RunSerializer, RunListSerializer


class RunListView(generics.ListAPIView):
    serializer_class = RunListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        team_ids = TeamMember.objects.filter(
            user=self.request.user
        ).values_list('team_id', flat=True)
        queryset = Run.objects.filter(job__team_id__in=team_ids)

        job_id = self.request.query_params.get('job_id')
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
