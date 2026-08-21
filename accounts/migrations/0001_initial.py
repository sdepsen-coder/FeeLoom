from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LegalAcceptance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.CharField(max_length=20)),
                ("accepted_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="legal_acceptances", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-accepted_at", "-id")},
        ),
        migrations.AddConstraint(
            model_name="legalacceptance",
            constraint=models.UniqueConstraint(fields=("user", "version"), name="unique_legal_acceptance_per_user_version"),
        ),
    ]
