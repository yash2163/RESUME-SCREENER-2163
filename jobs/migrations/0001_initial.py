from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="JobDescription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("summary", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-active", "name"]},
        ),
        migrations.CreateModel(
            name="Resume",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("message_id", models.CharField(max_length=255)),
                ("sender", models.EmailField(blank=True, max_length=254, null=True)),
                ("candidate_name", models.CharField(blank=True, max_length=255)),
                ("candidate_email", models.EmailField(blank=True, max_length=254, null=True)),
                ("candidate_phone", models.CharField(blank=True, max_length=50)),
                ("attachment_name", models.CharField(max_length=255)),
                ("text_content", models.TextField()),
                ("file_url", models.URLField(blank=True, max_length=500)),
                ("received_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-received_at"]},
        ),
        migrations.CreateModel(
            name="QualificationCriterion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("detail", models.TextField(blank=True)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="criteria", to="jobs.jobdescription")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="ResumeScore",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("total_score", models.FloatField(default=0)),
                ("detail", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="resume_scores", to="jobs.jobdescription")),
                ("resume", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="scores", to="jobs.resume")),
            ],
            options={"ordering": ["-total_score"], "unique_together": {("resume", "job")}},
        ),
        migrations.AlterUniqueTogether(
            name="resume",
            unique_together={("message_id", "attachment_name")},
        ),
    ]
