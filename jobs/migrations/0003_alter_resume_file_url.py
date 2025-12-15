from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0002_resume_candidate_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="resume",
            name="file_url",
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
