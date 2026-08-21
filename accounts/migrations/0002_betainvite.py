import accounts.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("workspaces", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BetaInvite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(default=accounts.models.default_invite_code, max_length=20, unique=True)),
                ("label", models.CharField(blank=True, max_length=120)),
                ("max_uses", models.PositiveSmallIntegerField(default=1)),
                ("use_count", models.PositiveSmallIntegerField(default=0)),
                ("expires_at", models.DateTimeField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="created_beta_invites", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="beta_invites", to="workspaces.workspace")),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
    ]
