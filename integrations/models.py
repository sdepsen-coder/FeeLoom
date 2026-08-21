from django.db import models

from workspaces.models import Shop


class EtsyConnection(models.Model):
    shop = models.OneToOneField(Shop, on_delete=models.CASCADE, related_name="etsy_connection")
    etsy_user_id = models.CharField(max_length=40)
    access_token_ciphertext = models.TextField()
    refresh_token_ciphertext = models.TextField()
    token_expires_at = models.DateTimeField()
    scopes = models.CharField(max_length=255, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"Etsy - {self.shop.name}"


class EtsySyncRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    connection = models.ForeignKey(EtsyConnection, on_delete=models.CASCADE, related_name="sync_runs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    imported_orders = models.PositiveIntegerField(default=0)
    updated_orders = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at", "-id")
