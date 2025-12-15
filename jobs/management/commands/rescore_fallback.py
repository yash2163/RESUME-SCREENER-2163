from django.core.management.base import BaseCommand

from jobs.models import ResumeScore
from jobs.services import GeminiEvaluator


class Command(BaseCommand):
    help = "Rescore resumes that were previously scored with the fallback keyword matcher."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, help="Limit rescoring to a specific JobDescription ID.")
        parser.add_argument(
            "--limit",
            type=int,
            help="Stop after processing this many fallback scores (helps for large backlogs).",
        )

    def handle(self, *args, **options):
        scorer = GeminiEvaluator()
        if not scorer.model:
            self.stdout.write(
                self.style.ERROR(
                    "Gemini/Vertex is not configured. Set GEMINI_API_KEY or Vertex settings, then rerun."
                )
            )
            return

        qs = ResumeScore.objects.select_related("resume", "job")
        if options.get("job"):
            qs = qs.filter(job_id=options["job"])

        fallback_scores = []
        for score in qs:
            detail = score.detail or []
            has_fallback_note = any((item.get("notes") or "").lower().startswith("fallback") for item in detail)
            if has_fallback_note or not detail:
                fallback_scores.append(score)
            if options.get("limit") and len(fallback_scores) >= options["limit"]:
                break

        if not fallback_scores:
            self.stdout.write("No fallback scores found to rescore.")
            return

        updated = 0
        for score in fallback_scores:
            total, detail = scorer.score_resume_against_job(score.resume.text_content, score.job)
            score.total_score = total
            score.detail = detail
            score.save(update_fields=["total_score", "detail"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Rescored {updated} fallback resume score(s)."))
