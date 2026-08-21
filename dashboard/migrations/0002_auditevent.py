from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_initial"),
        ("workspaces", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=80)),
                ("summary", models.CharField(blank=True, max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("request_id", models.CharField(blank=True, max_length=36)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("shop", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to="workspaces.shop")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="workspaces.workspace")),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["workspace", "-created_at"], name="dashboard_a_workspa_d98217_idx"),
        ),
    ]
