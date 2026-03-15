import uuid
from django.db import models
from apps.accounts.models import Team


class Job(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        ARCHIVED = 'archived', 'Archived'

    class ScheduleFrequency(models.TextChoices):
        DAILY = 'daily', 'Daily'
        WEEKLY = 'weekly', 'Weekly'
        MONTHLY = 'monthly', 'Monthly'

    class DeviceType(models.TextChoices):
        DESKTOP = 'desktop', 'Desktop'
        MOBILE = 'mobile', 'Mobile'
        BOTH = 'both', 'Both'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='jobs')
    name = models.CharField(max_length=255)
    product_url = models.URLField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    # Schedule
    schedule_frequency = models.CharField(
        max_length=20, choices=ScheduleFrequency.choices, default=ScheduleFrequency.WEEKLY
    )
    schedule_day = models.IntegerField(default=0, help_text='Day of week (0=Mon) or day of month')
    schedule_time = models.TimeField(null=True, blank=True)

    # Device
    device_type = models.CharField(max_length=20, choices=DeviceType.choices, default=DeviceType.DESKTOP)

    # Notifications
    notify_email = models.BooleanField(default=True)
    notify_slack = models.BooleanField(default=False)
    slack_webhook_url = models.URLField(blank=True, default='')

    # Analysis areas
    areas = models.JSONField(default=list, help_text='List of UX areas to analyze')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'jobs'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Competitor(models.Model):
    class AccessStatus(models.TextChoices):
        PUBLIC = 'public', 'Public'
        LOGIN_REQUIRED = 'login_required', 'Login Required'
        AUTHENTICATED = 'authenticated', 'Authenticated'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='competitors')
    name = models.CharField(max_length=255)
    url = models.URLField()
    access_status = models.CharField(
        max_length=20, choices=AccessStatus.choices, default=AccessStatus.PUBLIC
    )
    proxy_country = models.CharField(max_length=2, blank=True, default='')
    vault_path = models.CharField(max_length=255, blank=True, default='', help_text='Path to stored credentials')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'competitors'

    def __str__(self):
        return f'{self.name} ({self.url})'
