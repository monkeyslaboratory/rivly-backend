import uuid
from django.db import models
from apps.jobs.models import Job, Competitor


class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='runs')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'runs'
        ordering = ['-created_at']

    def __str__(self):
        return f'Run {self.id} - {self.job.name} ({self.status})'


class RunScreenshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name='screenshots')
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name='screenshots')
    image_url = models.URLField()
    device_type = models.CharField(max_length=20, default='desktop')
    page_title = models.CharField(max_length=500, blank=True, default='')
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'run_screenshots'

    def __str__(self):
        return f'Screenshot {self.id} - {self.competitor.name}'


class RunReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name='reports')
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name='reports')
    area = models.CharField(max_length=100)
    score = models.IntegerField(default=0, help_text='Score 0-100')
    summary = models.TextField(blank=True, default='')
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'run_reports'
        unique_together = ('run', 'competitor', 'area')

    def __str__(self):
        return f'{self.competitor.name} - {self.area}: {self.score}'


class RunOverallScore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name='overall_scores')
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name='overall_scores')
    score = models.IntegerField(default=0, help_text='Overall score 0-100')
    rank = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'run_overall_scores'
        unique_together = ('run', 'competitor')

    def __str__(self):
        return f'{self.competitor.name} - Overall: {self.score} (Rank {self.rank})'
