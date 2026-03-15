from rest_framework import serializers
from .models import Run, RunScreenshot, RunReport, RunOverallScore


class RunScreenshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunScreenshot
        fields = ['id', 'competitor', 'image_url', 'device_type', 'page_title', 'captured_at']
        read_only_fields = ['id', 'captured_at']


class RunReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunReport
        fields = ['id', 'competitor', 'area', 'score', 'summary', 'details', 'created_at']
        read_only_fields = ['id', 'created_at']


class RunOverallScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunOverallScore
        fields = ['id', 'competitor', 'score', 'rank', 'created_at']
        read_only_fields = ['id', 'created_at']


class RunSerializer(serializers.ModelSerializer):
    screenshots = RunScreenshotSerializer(many=True, read_only=True)
    reports = RunReportSerializer(many=True, read_only=True)
    overall_scores = RunOverallScoreSerializer(many=True, read_only=True)

    class Meta:
        model = Run
        fields = [
            'id', 'job', 'status', 'started_at', 'completed_at',
            'error_message', 'screenshots', 'reports', 'overall_scores', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class RunListSerializer(serializers.ModelSerializer):
    """Lighter serializer for list views without nested relations."""
    class Meta:
        model = Run
        fields = ['id', 'job', 'status', 'started_at', 'completed_at', 'error_message', 'created_at']
        read_only_fields = ['id', 'created_at']
