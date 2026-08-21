from django.conf import settings
from django.db import models


class LegalAcceptance(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="legal_acceptances",
    )
    version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-accepted_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "version"),
                name="unique_legal_acceptance_per_user_version",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.version}"
