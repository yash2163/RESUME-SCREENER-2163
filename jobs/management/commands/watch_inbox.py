import time

from django.core.management.base import BaseCommand

from jobs.services import M365InboxReader, ResumeIngestor


class Command(BaseCommand):
    help = "Continuously poll the M365 inbox for new resumes."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=300, help="Seconds between polls.")
        parser.add_argument("--batch", type=int, default=50, help="Messages per batch.")
        parser.add_argument(
            "--initial-full",
            action="store_true",
            help="On first loop, page through all messages with attachments.",
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        batch = options["batch"]
        initial_full = options["initial_full"]

        reader = M365InboxReader()
        ingestor = ResumeIngestor()

        if not reader.is_configured():
            self.stdout.write(
                self.style.ERROR(
                    "M365 credentials missing (tenant_id/client_id/client_secret). Update your environment and restart this worker."
                )
            )
            return

        self.stdout.write(
            self.style.NOTICE(
                f"Starting inbox watcher (interval={interval}s, batch={batch}, initial_full={initial_full})"
            )
        )
        first_pass = True
        while True:
            fetch_all = initial_full and first_pass
            attachments = reader.fetch_recent_attachments(limit=batch, fetch_all=fetch_all)
            if attachments:
                saved = ingestor.ingest(attachments)
                self.stdout.write(self.style.SUCCESS(f"Ingested {saved} new resume(s)."))
            else:
                self.stdout.write("No new attachments found.")
            first_pass = False
            time.sleep(interval)
