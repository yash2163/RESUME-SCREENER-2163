from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0003_alter_resume_file_url"),
    ]

    operations = [
        migrations.AlterField(
            model_name="resume",
            name="file_url",
            field=models.URLField(blank=True, max_length=2048),
        ),
    ]
