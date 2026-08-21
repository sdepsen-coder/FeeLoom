from django.conf import settings
from django.db import models


class Workspace(models.Model):
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=170, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_workspaces",
    )
    currency = models.CharField(max_length=3, default="USD")
    country_code = models.CharField(max_length=2, default="US")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        VIEWER = "viewer", "Viewer"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("workspace", "user"),
                name="unique_workspace_membership",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.workspace}"


class Shop(models.Model):
    class Marketplace(models.TextChoices):
        ETSY = "etsy", "Etsy"

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="shops",
    )
    name = models.CharField(max_length=160)
    marketplace = models.CharField(
        max_length=20,
        choices=Marketplace.choices,
        default=Marketplace.ETSY,
    )
    external_shop_id = models.CharField(max_length=100, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name", "id")

    def __str__(self):
        return self.name
