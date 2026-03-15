from rest_framework import serializers
from .models import Job, Competitor


class CompetitorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Competitor
        fields = ['id', 'name', 'url', 'access_status', 'proxy_country', 'vault_path', 'created_at']
        read_only_fields = ['id', 'created_at']


class JobSerializer(serializers.ModelSerializer):
    competitors = CompetitorSerializer(many=True, read_only=True)

    class Meta:
        model = Job
        fields = [
            'id', 'team', 'name', 'product_url', 'status',
            'schedule_frequency', 'schedule_day', 'schedule_time',
            'device_type', 'notify_email', 'notify_slack', 'slack_webhook_url',
            'areas', 'competitors', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AnalyzeProductRequestSerializer(serializers.Serializer):
    url = serializers.URLField()


class DiscoverCompetitorsRequestSerializer(serializers.Serializer):
    product_url = serializers.URLField()
    product_description = serializers.CharField(required=False, default='')


class SuggestAreasRequestSerializer(serializers.Serializer):
    product_url = serializers.URLField()
    competitors = serializers.ListField(child=serializers.URLField(), required=False, default=list)
