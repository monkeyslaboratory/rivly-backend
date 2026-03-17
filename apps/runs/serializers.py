from rest_framework import serializers
from .models import Run, RunScreenshot, RunReport, RunOverallScore, RunComparison


class RunScreenshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunScreenshot
        fields = [
            'id', 'competitor', 'page_url', 'page_name', 'device_type',
            's3_key', 'thumbnail_s3_key', 'viewport_width', 'viewport_height',
            'dom_text', 'html_snippet', 'status', 'error_message', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class RunReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunReport
        fields = [
            'id', 'competitor', 'category', 'score', 'score_breakdown',
            'summary', 'details', 'recommendations',
            'previous_report', 'previous_score', 'score_delta', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class RunOverallScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunOverallScore
        fields = [
            'id', 'competitor', 'overall_score',
            'previous_overall_score', 'score_delta',
            'category_scores', 'top_insights', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class RunComparisonSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunComparison
        fields = [
            'id', 'executive_summary', 'feature_matrix', 'flow_comparison',
            'ux_scorecard', 'recommendations', 'competitive_position', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class RunSerializer(serializers.ModelSerializer):
    screenshots = RunScreenshotSerializer(many=True, read_only=True)
    reports = RunReportSerializer(many=True, read_only=True)
    overall_scores = RunOverallScoreSerializer(many=True, read_only=True)
    comparison = RunComparisonSerializer(read_only=True)
    has_auth_pages = serializers.SerializerMethodField()
    auth_cookies = serializers.SerializerMethodField()

    def get_has_auth_pages(self, obj):
        return obj.screenshots.filter(status='auth_required').exists()

    def get_auth_cookies(self, obj):
        """Return list (not contents) so frontend knows if cookies exist."""
        return obj.auth_cookies if obj.auth_cookies else []

    class Meta:
        model = Run
        fields = [
            'id', 'job', 'triggered_by', 'status', 'progress', 'current_phase',
            'started_at', 'completed_at', 'duration_seconds', 'cost_api_usd',
            'error_log', 'retry_count',
            'screenshots', 'reports', 'overall_scores', 'comparison',
            'has_auth_pages', 'auth_status', 'auth_message', 'auth_cookies', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class RunListSerializer(serializers.ModelSerializer):
    """Lighter serializer for list views without nested relations."""
    class Meta:
        model = Run
        fields = [
            'id', 'job', 'triggered_by', 'status', 'progress', 'current_phase',
            'started_at', 'completed_at', 'duration_seconds', 'cost_api_usd',
            'error_log', 'retry_count', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']
