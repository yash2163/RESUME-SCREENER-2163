from django.core.management.base import BaseCommand

from jobs.services import M365InboxReader, ResumeIngestor


class Command(BaseCommand):
    help = "Read resumes from M365 inbox, score against active JDs, and store results."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=5, help="Number of messages to inspect.")

    def handle(self, *args, **options):
        limit = options["limit"]
        reader = M365InboxReader()
        if not reader.is_configured():
            self.stdout.write(
                self.style.ERROR(
                    "M365 credentials missing (tenant_id/client_id/client_secret). Update your environment and restart the worker."
                )
            )
            return
        attachments = reader.fetch_recent_attachments(limit=limit)
        if not attachments:
            self.stdout.write(
                self.style.WARNING(
                    "No attachments fetched. Confirm the inbox has PDF/TXT attachments and that the app has Graph Mail.Read permissions."
                )
            )
            return

        ingestor = ResumeIngestor()
        saved = ingestor.ingest(attachments)
        self.stdout.write(self.style.SUCCESS(f"Ingested {saved} new resume(s)."))
