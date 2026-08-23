from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0002_auditevent")]

    operations = [
        migrations.AlterField(
            model_name="feedback",
            name="category",
            field=models.CharField(
                choices=[
                    ("calculation", "Calculation issue"),
                    ("usability", "Something is confusing"),
                    ("missing_feature", "Missing feature"),
                    ("bug", "Something is broken"),
                    ("other", "General feedback"),
                ],
                max_length=30,
            ),
        ),
    ]
