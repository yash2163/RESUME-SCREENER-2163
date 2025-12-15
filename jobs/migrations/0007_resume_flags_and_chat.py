from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("jobs", "0006_schema_cleanup"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        'ALTER TABLE jobs_resume ADD COLUMN IF NOT EXISTS is_active_seeker boolean NOT NULL DEFAULT false;',
                        'ALTER TABLE jobs_resume ADD COLUMN IF NOT EXISTS is_favorite boolean NOT NULL DEFAULT false;',
                        "ALTER TABLE jobs_resume ADD COLUMN IF NOT EXISTS source varchar(50) NOT NULL DEFAULT 'email';",
                    ],
                    reverse_sql=[
                        'ALTER TABLE jobs_resume DROP COLUMN IF EXISTS is_active_seeker;',
                        'ALTER TABLE jobs_resume DROP COLUMN IF EXISTS is_favorite;',
                        'ALTER TABLE jobs_resume DROP COLUMN IF EXISTS source;',
                    ],
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="resume",
                    name="is_active_seeker",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="resume",
                    name="is_favorite",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="resume",
                    name="source",
                    field=models.CharField(
                        choices=[("email", "Email"), ("upload", "Upload"), ("manual", "Manual")],
                        default="email",
                        max_length=50,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ChatSession",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_key", models.CharField(max_length=64, unique=True)),
                ("title", models.CharField(blank=True, max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="ErrorLog",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("location", models.CharField(max_length=255)),
                ("message", models.TextField()),
                ("details", models.TextField(blank=True)),
                ("context", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved", models.BooleanField(default=False)),
                (
                    "resume",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="error_logs",
                        to="jobs.resume",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ChatMessage",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("user", "User"), ("assistant", "Assistant")], max_length=20)),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="jobs.chatsession"
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
