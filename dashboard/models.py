from django.conf import settings
from django.db import models

from workspaces.models import Shop, Workspace


class Feedback(models.Model):
    class Category(models.TextChoices):
        CALCULATION = "calculation", "Calculation issue"
        USABILITY = "usability", "Something is confusing"
        MISSING_FEATURE = "missing_feature", "Missing feature"
        BUG = "bug", "Something is broken"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        NEW = "new", "New"
        REVIEWING = "reviewing", "Reviewing"
        RESOLVED = "resolved", "Resolved"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="feedback")
    shop = models.ForeignKey(
        Shop,
        on_delete=models.SET_NULL,
        related_name="feedback",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submitted_feedback",
    )
    category = models.CharField(max_length=30, choices=Category.choices)
    rating = models.PositiveSmallIntegerField(
        choices=[(value, str(value)) for value in range(1, 6)]
    )
    message = models.TextField(max_length=3000)
    page_path = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.get_category_display()} - {self.workspace.name}"
