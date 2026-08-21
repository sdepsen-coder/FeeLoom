from django.conf import settings
from django.db import models
from django.utils import timezone
import secrets


def default_invite_code():
    return secrets.token_hex(6).upper()


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


class BetaInvite(models.Model):
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="beta_invites",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_beta_invites",
    )
    code = models.CharField(max_length=20, unique=True, default=default_invite_code)
    label = models.CharField(max_length=120, blank=True)
    max_uses = models.PositiveSmallIntegerField(default=1)
    use_count = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")

    @property
    def is_available(self):
        return self.is_active and self.use_count < self.max_uses and self.expires_at > timezone.now()

    def __str__(self):
        return self.label or self.code
