from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0005_alter_resume_file_url_length"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                # Ensure file_url has sufficient length
                "ALTER TABLE jobs_resume ALTER COLUMN file_url TYPE varchar(2048);",
                # Drop legacy columns if they still exist from earlier schema
                "ALTER TABLE jobs_qualificationcriterion DROP COLUMN IF EXISTS title;",
                "ALTER TABLE jobs_qualificationcriterion DROP COLUMN IF EXISTS weight;",
                "ALTER TABLE jobs_qualificationcriterion DROP COLUMN IF EXISTS \"order\";",
            ],
            reverse_sql=[
                # No reverse necessary; keep forward-only
            ],
        ),
    ]
