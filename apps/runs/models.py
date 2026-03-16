import uuid
from django.db import models
from apps.jobs.models import Job, Competitor


class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        PREFLIGHT = "preflight"
        SCREENSHOTS = "screenshots"
        DISCOVERED = "discovered"
        APPROVED = "approved"
        ANALYZING = "analyzing"
        SCORING = "scoring"
        COMPARING = "comparing"
        COMPLETED = "completed"
        PARTIAL = "partial"
        FAILED = "failed"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="runs")
    triggered_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    progress = models.IntegerField(default=0)
    current_phase = models.CharField(max_length=50, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    cost_api_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    error_log = models.TextField(blank=True, default="")
    retry_count = models.IntegerField(default=0)
    # Credentials for authenticated crawl (encrypted in production, plain for dev)
    auth_credentials = models.JSONField(default=dict, blank=True)
    # {"url": "https://...", "email": "...", "password": "...", "login_url": "..."}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "runs"
        ordering = ["-created_at"]


class RunScreenshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="screenshots")
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name="screenshots")
    page_url = models.URLField()
    page_name = models.CharField(max_length=100)
    device_type = models.CharField(max_length=10)
    s3_key = models.CharField(max_length=512)
    thumbnail_s3_key = models.CharField(max_length=512, blank=True, default="")
    viewport_width = models.IntegerField()
    viewport_height = models.IntegerField()
    dom_text = models.TextField(blank=True, default="")
    html_snippet = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, default="success")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "run_screenshots"


class RunReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="reports")
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name="reports")
    category = models.CharField(max_length=100)
    score = models.IntegerField(default=0)
    score_breakdown = models.JSONField(default=dict)
    summary = models.TextField(default="")
    details = models.JSONField(default=list)
    recommendations = models.JSONField(default=list)
    previous_report = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="next_report")
    previous_score = models.IntegerField(null=True, blank=True)
    score_delta = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "run_reports"


class RunOverallScore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="overall_scores")
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name="overall_scores")
    overall_score = models.IntegerField()
    previous_overall_score = models.IntegerField(null=True, blank=True)
    score_delta = models.IntegerField(null=True, blank=True)
    category_scores = models.JSONField(default=dict)
    top_insights = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "run_overall_scores"
        unique_together = ("run", "competitor")


class RunComparison(models.Model):
    """Comparative analysis across all competitors for a run."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(Run, on_delete=models.CASCADE, related_name="comparison")

    executive_summary = models.TextField(default="")
    feature_matrix = models.JSONField(default=list)
    # [{"feature": "...", "our_product": "yes/no/partial", "competitors": {"name": "yes/no/partial"}}]

    flow_comparison = models.JSONField(default=list)
    # [{"flow": "Registration", "products": {"Our": {"steps": 3, "friction": "low"}, "Comp A": {...}}}]

    ux_scorecard = models.JSONField(default=dict)
    # {"dimensions": [...], "scores": {"Our Product": {...}, "Comp A": {...}}}

    recommendations = models.JSONField(default=list)
    # [{"finding": "...", "evidence": "...", "impact": "...", "priority": "...", "recommendation": "..."}]

    competitive_position = models.TextField(default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "run_comparisons"
