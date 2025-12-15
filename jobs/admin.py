from django.contrib import admin

from .models import ChatMessage, ChatSession, ErrorLog, JobDescription, QualificationCriterion, Resume, ResumeScore


class QualificationCriterionInline(admin.TabularInline):
    model = QualificationCriterion
    extra = 1


@admin.register(JobDescription)
class JobDescriptionAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "created_at")
    list_filter = ("active",)
    inlines = [QualificationCriterionInline]


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("attachment_name", "candidate_email", "candidate_phone", "received_at", "is_active_seeker", "is_favorite", "source")
    search_fields = ("attachment_name", "sender", "candidate_email", "candidate_phone")
    list_filter = ("is_active_seeker", "is_favorite", "source")


@admin.register(ResumeScore)
class ResumeScoreAdmin(admin.ModelAdmin):
    list_display = ("resume", "job", "total_score", "created_at")
    list_filter = ("job",)


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("session_key", "user", "created_at", "updated_at")
    search_fields = ("session_key", "user__username")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "created_at")
    search_fields = ("content",)
    list_filter = ("role",)


@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin):
    list_display = ("location", "message", "created_at", "resolved")
    list_filter = ("resolved",)
    search_fields = ("location", "message")
