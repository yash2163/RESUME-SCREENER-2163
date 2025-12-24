from django.conf import settings
from django.db import models


class ResumeSource(models.TextChoices):
    EMAIL = "email", "Email"
    UPLOAD = "upload", "Upload"
    MANUAL = "manual", "Manual"


class JobDescription(models.Model):
    name = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-active", "name"]

    def __str__(self) -> str:
        return self.name


class QualificationCriterion(models.Model):
    job = models.ForeignKey(JobDescription, related_name="criteria", on_delete=models.CASCADE)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.job.name} criterion {self.id}"


class Resume(models.Model):
    # --- Standard Fields ---
    message_id = models.CharField(max_length=255)
    sender = models.EmailField(blank=True, null=True)
    candidate_name = models.CharField(max_length=255, blank=True)
    candidate_email = models.EmailField(blank=True, null=True)
    candidate_phone = models.CharField(max_length=50, blank=True)
    is_active_seeker = models.BooleanField(default=False)
    is_favorite = models.BooleanField(default=False)
    source = models.CharField(
        max_length=50,
        choices=ResumeSource.choices,
        default=ResumeSource.EMAIL,
    )
    attachment_name = models.CharField(max_length=255)
    text_content = models.TextField()
    file_url = models.URLField(blank=True, max_length=2048)
    received_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    # --- ENHANCED TRACKING FIELDS ---
    STATUS_CHOICES = [
        ('NEW', 'New / Pending'),             # Just arrived, not scored yet
        ('SHORTLISTED', 'Shortlisted'),
        ('AUTO_REJECTED', 'Auto Rejected'),   # AI Score < Threshold
        ('SHORTLISTED', 'Shortlisted'),       # AI Score > Threshold, waiting for action
        ('INTERVIEW_SCHEDULED', 'Interview Scheduled'), # Email sent
        ('INTERVIEW_PASSED', 'Interview Passed'),       # Good interview
        ('INTERVIEW_REJECTED', 'Rejected after Interview'), # Bad interview
        ('HIRED', 'Hired'),                   # Final Success
    ]
    
    status = models.CharField(
        max_length=30, 
        choices=STATUS_CHOICES, 
        default='NEW'
    )
    
    # --- Email Audit Trails ---
    # These flags ensure we don't accidentally spam candidates if we re-save the resume
    rejection_email_sent = models.BooleanField(default=False)
    interview_email_sent = models.BooleanField(default=False)
    
    # --- Interview Logistics ---
    interview_date = models.DateTimeField(null=True, blank=True)
    interview_link = models.CharField(max_length=255, blank=True)
    
    # --- Human Feedback ---
    # To store notes like: "Good communication, but weak in Python"
    recruiter_notes = models.TextField(blank=True)

    # human scores
    human_score = models.FloatField(null=True, blank=True)
    human_score_breakdown = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("message_id", "attachment_name")
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.candidate_name} - {self.get_status_display()}"


class ResumeScore(models.Model):
    resume = models.ForeignKey(Resume, related_name="scores", on_delete=models.CASCADE)
    job = models.ForeignKey(JobDescription, related_name="resume_scores", on_delete=models.CASCADE)
    total_score = models.FloatField(default=0)
    detail = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("resume", "job")
        ordering = ["-total_score"]

    def __str__(self) -> str:
        return f"{self.resume} -> {self.job} ({self.total_score})"


class ChatSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="chat_sessions", on_delete=models.CASCADE)
    session_key = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or f"Chat {self.session_key}"


class ChatMessage(models.Model):
    ROLE_CHOICES = (("user", "User"), ("assistant", "Assistant"))
    session = models.ForeignKey(ChatSession, related_name="messages", on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:30]}"


class ErrorLog(models.Model):
    location = models.CharField(max_length=255)
    message = models.TextField()
    details = models.TextField(blank=True)
    context = models.JSONField(default=dict, blank=True)
    resume = models.ForeignKey(Resume, related_name="error_logs", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.location}: {self.message[:50]}"


# jobs/models.py

from django.conf import settings

class ResumeActivityLog(models.Model):
    ACTION_CHOICES = [
        ('INGESTED', 'Resume Ingested'),
        ('SCORED', 'AI Scored'),
        ('RESCORED', 'Rescored'),
        ('STATUS_CHANGE', 'Status Changed'),
        ('EMAIL_SENT', 'Email Sent'),
        ('NOTE_ADDED', 'Note Added'),
        ('FAVORITE', 'Marked as Favorite'),
        ('UNFAVORITE', 'Unmarked Favorite'),
        ('RATED', 'Manager Rated'),
    ]

    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name='logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action_type = models.CharField(max_length=20, choices=ACTION_CHOICES)
    details = models.TextField(blank=True, null=True)  # E.g., "Score: 85" or "Subject: Interview Invite"
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.get_action_type_display()} - {self.resume.candidate_name}"
