import uuid
from django.db import models


class ProxyNode(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'
        MAINTENANCE = 'maintenance', 'Maintenance'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    host = models.CharField(max_length=255)
    port = models.IntegerField()
    country = models.CharField(max_length=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    last_health_check = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'proxy_nodes'

    def __str__(self):
        return f'{self.name} ({self.country})'
