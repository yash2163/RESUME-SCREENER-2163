import json
import csv
import logging
import threading
import re
import uuid

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.core.mail import send_mail
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_datetime

from .forms import (
    JobDescriptionForm,
    QualificationFormSet,
    RememberMeAuthenticationForm,
    ResumeUploadForm,
)

from django.views.decorators.http import require_POST

from .models import ChatMessage, ChatSession, ErrorLog, JobDescription, Resume, ResumeScore
from .services import (
    AttachmentPayload, 
    ResumeIngestor, 
    GeminiEvaluator, 
    GoogleStorageUploader, 
    populate_resume_text,
    check_and_process_automation  # <--- IMPORTED AUTOMATION LOGIC
)


from django.conf import settings
logger = logging.getLogger(__name__)


def remember_me_login(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RememberMeAuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        if not form.cleaned_data.get("remember_me"):
            request.session.set_expiry(0)
        return redirect(request.GET.get("next") or "dashboard")
    return render(request, "registration/login.html", {"form": form})


@login_required
def job_list(request):
    jobs = JobDescription.objects.prefetch_related("criteria").all()
    return render(request, "jobs/job_list.html", {"jobs": jobs})


@login_required
def job_create(request):
    job = JobDescription()
    if request.method == "POST":
        form = JobDescriptionForm(request.POST, instance=job)
        formset = QualificationFormSet(request.POST, instance=job)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Job description created.")
            return redirect("job_list")
    else:
        form = JobDescriptionForm(instance=job)
        formset = QualificationFormSet(instance=job)
    return render(
        request,
        "jobs/job_form.html",
        {"form": form, "formset": formset, "title": "Create Job Description"},
    )


@login_required
def job_edit(request, pk):
    job = get_object_or_404(JobDescription, pk=pk)
    if request.method == "POST":
        form = JobDescriptionForm(request.POST, instance=job)
        formset = QualificationFormSet(request.POST, instance=job)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Job description updated.")
            return redirect("job_list")
    else:
        form = JobDescriptionForm(instance=job)
        formset = QualificationFormSet(instance=job)
    return render(
        request,
        "jobs/job_form.html",
        {"form": form, "formset": formset, "title": f"Edit {job.name}"},
    )


@login_required
def toggle_job_active(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    job = get_object_or_404(JobDescription, pk=pk)
    job.active = not job.active
    job.save(update_fields=["active"])
    messages.info(request, f"{job.name} is now {'active' if job.active else 'inactive'}.")
    return HttpResponseRedirect(reverse("job_list"))


@login_required
def rescore_job(request, pk):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    job = get_object_or_404(JobDescription, pk=pk)

    def run_rescore(job_obj: JobDescription):
        """Background rescore to avoid blocking the request."""
        from django.db import close_old_connections

        close_old_connections()
        scorer = GeminiEvaluator()
        qs = Resume.objects.all().iterator(chunk_size=100)
        processed = 0
        for resume in qs:
            try:
                if not resume.text_content:
                    continue
                total, detail = scorer.score_resume_against_job(resume.text_content, job_obj)
                
                # --- UPDATE: Capture object and trigger automation ---
                score_obj, created = ResumeScore.objects.update_or_create(
                    resume=resume, job=job_obj, defaults={"total_score": total, "detail": detail}
                )
                
                # Trigger Auto-Reject / Shortlist Logic
                check_and_process_automation(score_obj)
                
                processed += 1
            except Exception as exc:
                logger.exception(
                    "Job rescore failed for resume",
                    extra={"job_id": job_obj.id, "resume_id": resume.id, "error": str(exc)},
                )
        logger.info("Job rescore complete", extra={"job_id": job_obj.id, "processed": processed})
        close_old_connections()

    threading.Thread(target=run_rescore, args=(job,), daemon=True).start()
    messages.info(request, f"Rescore started for {job.name}. Results will update shortly.")
    return redirect(request.META.get("HTTP_REFERER", reverse("job_list")))


@login_required
def rescore_single_score(request, score_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    score = get_object_or_404(ResumeScore.objects.select_related("resume", "job"), pk=score_id)
    scorer = GeminiEvaluator()

    def respond(success: bool, message: str, payload=None, status=200):
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            data = {"success": success, "message": message}
            if payload:
                data.update(payload)
            return JsonResponse(data, status=status if success else 400)
        if success:
            messages.success(request, message)
        else:
            messages.error(request, message)
        return redirect(request.META.get("HTTP_REFERER", reverse("dashboard")))

    if not score.resume.text_content:
        return respond(False, "No resume text available to rescore.")

    try:
        total, detail = scorer.score_resume_against_job(score.resume.text_content, score.job)
        score.total_score = total
        score.detail = detail
        score.save(update_fields=["total_score", "detail"])
        
        # --- UPDATE: Trigger Auto-Reject / Shortlist Logic ---
        check_and_process_automation(score)
        
        return respond(
            True,
            f"Rescored {score.resume.attachment_name} for {score.job.name}.",
            payload={"total_score": total},
        )
    except Exception as exc:
        logger.exception("Individual rescore failed", extra={"score_id": score_id, "error": str(exc)})
        return respond(False, "Rescore failed. Please try again.")


# @login_required
# def dashboard(request):
#     jobs = JobDescription.objects.all()
#     selected_job_id = request.GET.get("job")
#     selected_job = None
#     apply_flag = request.GET.get("apply")
#     show_results = bool(apply_flag or request.GET.get("page"))
#     scores = ResumeScore.objects.select_related("resume", "job")
#     sort = request.GET.get("sort", "score_date")
#     view_mode = request.GET.get("view", "tiles")
#     page = int(request.GET.get("page", 1))
#     page_size = min(int(request.GET.get("page_size", 20)), 100)
#     favorites_only = request.GET.get("favorites")
    
#     if selected_job_id:
#         selected_job = get_object_or_404(JobDescription, pk=selected_job_id)
#         scores = scores.filter(job=selected_job)
#     else:
#         scores = scores.filter(job__active=True)
    
#     # Apply favorites filter if enabled
#     if favorites_only:
#         scores = scores.filter(resume__is_favorite=True)

#     ordering_map = {
#         "score_desc": ("-total_score",),
#         "score_asc": ("total_score",),
#         "date_desc": ("-resume__received_at",),
#         "date_asc": ("resume__received_at",),
#         "score_date": ("-total_score", "-resume__received_at"),
#         "date_score": ("-resume__received_at", "-total_score"),
#     }
#     criteria_map = {}
#     paginator = Paginator([], page_size)
#     page_obj = paginator.get_page(1)
#     visible_scores = []
#     if show_results:
#         scores = scores.order_by(*ordering_map.get(sort, ("-total_score", "-resume__received_at")))
#         if selected_job:
#             criteria_map = {c.id: c for c in selected_job.criteria.all()}
#         paginator = Paginator(scores, page_size)
#         page_obj = paginator.get_page(page)
#         uploader = GoogleStorageUploader()
#         visible_scores = list(page_obj.object_list)
#         for score in visible_scores:
#             resume = score.resume
#             signed = None
#             if resume.file_url:
#                 if resume.file_url.startswith("http"):
#                     signed = resume.file_url
#                 else:
#                     signed = uploader.signed_url_for_blob(resume.file_url)
#             resume.signed_url = signed
#             if not resume.text_content:
#                 populate_resume_text(resume, uploader)

#     chat_session = None
#     chat_messages = []
#     session_key = request.session.get("chat_session_key")
#     if session_key:
#         chat_session = ChatSession.objects.filter(session_key=session_key, user=request.user).first()
#     if not chat_session:
#         chat_session = ChatSession.objects.create(
#             user=request.user,
#             session_key=uuid.uuid4().hex,
#             metadata={"job_id": selected_job.id if selected_job else None},
#         )
#         request.session["chat_session_key"] = chat_session.session_key
#     elif selected_job and chat_session.metadata.get("job_id") != selected_job.id:
#         metadata = chat_session.metadata or {}
#         metadata["job_id"] = selected_job.id
#         chat_session.metadata = metadata
#         chat_session.save(update_fields=["metadata", "updated_at"])
#     chat_messages = list(chat_session.messages.order_by("created_at")[:20])

#     if request.headers.get("x-requested-with") == "XMLHttpRequest":
#         template_name = "jobs/partials/score_cards.html" if view_mode == "tiles" else "jobs/partials/score_rows.html"
#         html = render_to_string(
#             template_name,
#             {
#                 "scores": visible_scores,
#                 "selected_job": selected_job,
#                 "criteria_map": criteria_map,
#                 "view_mode": view_mode,
#             },
#             request=request,
#         )
#         return JsonResponse(
#             {
#                 "html": html,
#                 "has_next": page_obj.has_next() if show_results else False,
#                 "next_page": page_obj.next_page_number() if show_results and page_obj.has_next() else None,
#                 "added": len(visible_scores),
#                 "end_index": page_obj.end_index() if show_results else 0,
#                 "total_count": paginator.count if show_results else 0,
#             }
#         )
#     return render(
#         request,
#         "jobs/dashboard.html",
#         {
#             "jobs": jobs,
#             "selected_job": selected_job,
#             "scores": visible_scores,
#             "criteria_map": criteria_map,
#             "sort": sort,
#             "view_mode": view_mode,
#             "page_obj": page_obj,
#             "total_count": paginator.count if show_results else 0,
#             "page_size": page_size,
#             "chat_session": chat_session,
#             "chat_messages": chat_messages,
#             "show_results": show_results,
#         },
#     )


@require_POST
def shortlist_candidate(request, pk):
    resume = get_object_or_404(Resume, pk=pk)
    
    # Update status to SHORTLISTED
    resume.status = 'SHORTLISTED'
    resume.save()
    
    # Return success (HTMX or standard JS will handle the UI update)
    return JsonResponse({'status': 'success', 'message': 'Candidate shortlisted'})

# import json

@require_POST
def update_human_score(request, pk):
    resume = get_object_or_404(Resume, pk=pk)
    
    try:
        data = json.loads(request.body)
        breakdown = data.get('breakdown', {})
        total_score = data.get('total_score', 0)
        
        resume.human_score_breakdown = breakdown
        resume.human_score = total_score
        resume.save()
        
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

# @login_required
# def dashboard(request):
#     jobs = JobDescription.objects.all()
#     selected_job_id = request.GET.get("job")
#     selected_job = None
    
#     # --- 1. Get Base Queryset ---
#     scores = ResumeScore.objects.select_related("resume", "job")
    
#     if selected_job_id:
#         selected_job = get_object_or_404(JobDescription, pk=selected_job_id)
#         scores = scores.filter(job=selected_job)
#     else:
#         scores = scores.filter(job__active=True)

#     # --- 2. Filter by Tab (Status) ---
#     current_tab = request.GET.get("tab", "inbox") # Default to Inbox
    
#     if current_tab == "interviews":
#         # Show candidates who are in the interview stage
#         scores = scores.filter(resume__status__in=['INTERVIEW_SCHEDULED', 'INTERVIEW_PASSED', 'INTERVIEW_REJECTED'])
    
#     elif current_tab == "rejected":
#         # Show auto-rejected or manually rejected
#         scores = scores.filter(resume__status__in=['AUTO_REJECTED', 'REJECTED'])
        
#     elif current_tab == "hired":
#         scores = scores.filter(resume__status='HIRED')
        
#     else: # "inbox"
#         # Show New, Pending, and Shortlisted (The Active Pile)
#         # We exclude rejected/hired/interviewing to keep this view clean
#         scores = scores.exclude(resume__status__in=['AUTO_REJECTED', 'REJECTED', 'INTERVIEW_SCHEDULED', 'HIRED'])

#     # --- 3. Existing Filters (Favorites, Sort, etc.) ---
#     favorites_only = request.GET.get("favorites")
#     if favorites_only:
#         scores = scores.filter(resume__is_favorite=True)

#     apply_flag = request.GET.get("apply")
#     show_results = bool(apply_flag or request.GET.get("page") or current_tab != "inbox") # Auto-load if not on default tab
    
#     sort = request.GET.get("sort", "score_date")
#     view_mode = request.GET.get("view", "tiles")
#     page = int(request.GET.get("page", 1))
#     page_size = min(int(request.GET.get("page_size", 20)), 100)

#     ordering_map = {
#         "score_desc": ("-total_score",),
#         "score_asc": ("total_score",),
#         "date_desc": ("-resume__received_at",),
#         "date_asc": ("resume__received_at",),
#         "score_date": ("-total_score", "-resume__received_at"),
#         "date_score": ("-resume__received_at", "-total_score"),
#     }
    
#     criteria_map = {}
#     paginator = Paginator([], page_size)
#     page_obj = paginator.get_page(1)
#     visible_scores = []

#     # If apply clicked OR we are looking at specific tabs (like interviews), load data immediately
#     if show_results or current_tab != 'inbox':
#         scores = scores.order_by(*ordering_map.get(sort, ("-total_score", "-resume__received_at")))
#         if selected_job:
#             criteria_map = {c.id: c for c in selected_job.criteria.all()}
        
#         paginator = Paginator(scores, page_size)
#         page_obj = paginator.get_page(page)
#         visible_scores = list(page_obj.object_list)

#         # Hydrate text/urls
#         uploader = GoogleStorageUploader()
#         for score in visible_scores:
#             resume = score.resume
#             signed = None
#             if resume.file_url:
#                 if resume.file_url.startswith("http"):
#                     signed = resume.file_url
#                 else:
#                     signed = uploader.signed_url_for_blob(resume.file_url)
#             resume.signed_url = signed
#             if not resume.text_content:
#                 populate_resume_text(resume, uploader)

#     # Chat Session Logic (Unchanged)
#     chat_session = None
#     chat_messages = []
#     session_key = request.session.get("chat_session_key")
#     if session_key:
#         chat_session = ChatSession.objects.filter(session_key=session_key, user=request.user).first()
#     if not chat_session:
#         chat_session = ChatSession.objects.create(
#             user=request.user,
#             session_key=uuid.uuid4().hex,
#             metadata={"job_id": selected_job.id if selected_job else None},
#         )
#         request.session["chat_session_key"] = chat_session.session_key
#     elif selected_job and chat_session.metadata.get("job_id") != selected_job.id:
#         metadata = chat_session.metadata or {}
#         metadata["job_id"] = selected_job.id
#         chat_session.metadata = metadata
#         chat_session.save(update_fields=["metadata", "updated_at"])
#     chat_messages = list(chat_session.messages.order_by("created_at")[:20])

#     if request.headers.get("x-requested-with") == "XMLHttpRequest":
#         template_name = "jobs/partials/score_cards.html" if view_mode == "tiles" else "jobs/partials/score_rows.html"
#         html = render_to_string(
#             template_name,
#             {
#                 "scores": visible_scores,
#                 "selected_job": selected_job,
#                 "criteria_map": criteria_map,
#                 "view_mode": view_mode,
#             },
#             request=request,
#         )
#         return JsonResponse(
#             {
#                 "html": html,
#                 "has_next": page_obj.has_next() if show_results else False,
#                 "next_page": page_obj.next_page_number() if show_results and page_obj.has_next() else None,
#                 "added": len(visible_scores),
#                 "end_index": page_obj.end_index() if show_results else 0,
#                 "total_count": paginator.count if show_results else 0,
#             }
#         )
        
#     return render(
#         request,
#         "jobs/dashboard.html",
#         {
#             "jobs": jobs,
#             "selected_job": selected_job,
#             "scores": visible_scores,
#             "criteria_map": criteria_map,
#             "sort": sort,
#             "view_mode": view_mode,
#             "page_obj": page_obj,
#             "total_count": paginator.count if show_results else 0,
#             "page_size": page_size,
#             "chat_session": chat_session,
#             "chat_messages": chat_messages,
#             "show_results": show_results,
#             "current_tab": current_tab, # <-- Pass the tab to the template
#         },
#     )


@login_required
def export_scores_csv(request):
    selected_job_id = request.GET.get("job")
    scores = ResumeScore.objects.select_related("resume", "job")
    if selected_job_id:
        scores = scores.filter(job_id=selected_job_id)
    else:
        scores = scores.filter(job__active=True)
    scores = scores.order_by("-total_score", "-resume__received_at")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="resume_scores.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Job",
            "Resume",
            "Candidate Name",
            "Email",
            "Phone",
            "Received At",
            "Total Score",
            "File URL",
            "Criteria Breakdown",
        ]
    )
    uploader = GoogleStorageUploader()
    for score in scores:
        signed = None
        if score.resume.file_url:
            if score.resume.file_url.startswith("http"):
                signed = score.resume.file_url
            else:
                signed = uploader.signed_url_for_blob(score.resume.file_url)
        breakdown = "; ".join(
            f"{item.get('title')}: {item.get('score')}" for item in score.detail or []
        )
        writer.writerow(
            [
                score.job.name,
                score.resume.attachment_name,
                score.resume.candidate_name,
                score.resume.candidate_email or score.resume.sender,
                score.resume.candidate_phone,
                score.resume.received_at.isoformat(),
                score.total_score,
                signed or score.resume.file_url,
                breakdown,
            ]
        )
    return response

@login_required
def dashboard(request):
    jobs = JobDescription.objects.all()
    selected_job_id = request.GET.get("job")
    selected_job = None
    
    # --- 1. Get Base Queryset ---
    # We start with all scores and join with resume/job for performance
    scores = ResumeScore.objects.select_related("resume", "job")
    
    if selected_job_id:
        selected_job = get_object_or_404(JobDescription, pk=selected_job_id)
        scores = scores.filter(job=selected_job)
    else:
        scores = scores.filter(job__active=True)

    # --- 2. Filter by Tab (Status Logic Updated) ---
    current_tab = request.GET.get("tab", "inbox") # Default to Inbox
    
    if current_tab == "shortlisted":
        # NEW: Only show manually shortlisted candidates
        scores = scores.filter(resume__status='SHORTLISTED')

    elif current_tab == "interviews":
        # Show candidates in the interview loop
        scores = scores.filter(resume__status__in=['INTERVIEW_SCHEDULED', 'INTERVIEW_PASSED', 'INTERVIEW_REJECTED'])
    
    elif current_tab == "rejected":
        # Show auto-rejected or manually rejected
        scores = scores.filter(resume__status__in=['AUTO_REJECTED', 'REJECTED'])
        
    elif current_tab == "hired":
        # Show hired candidates
        scores = scores.filter(resume__status='HIRED')
        
    else: # "inbox"
        # DEFAULT: Show ONLY 'New' candidates.
        # This ensures Shortlisted people disappear from here.
        scores = scores.filter(resume__status='NEW')

    # --- 3. Existing Filters (Favorites, Sort, etc.) ---
    favorites_only = request.GET.get("favorites")
    if favorites_only:
        scores = scores.filter(resume__is_favorite=True)

    apply_flag = request.GET.get("apply")
    # Auto-load results if we are not on the default blank inbox, or if user clicked 'Apply'
    show_results = bool(apply_flag or request.GET.get("page") or current_tab != "inbox")
    
    sort = request.GET.get("sort", "score_date")
    view_mode = request.GET.get("view", "tiles")
    page = int(request.GET.get("page", 1))
    page_size = min(int(request.GET.get("page_size", 20)), 100)

    ordering_map = {
        "score_desc": ("-total_score",),
        "score_asc": ("total_score",),
        "date_desc": ("-resume__received_at",),
        "date_asc": ("resume__received_at",),
        "score_date": ("-total_score", "-resume__received_at"),
        "date_score": ("-resume__received_at", "-total_score"),
    }
    
    criteria_map = {}
    paginator = Paginator([], page_size)
    page_obj = paginator.get_page(1)
    visible_scores = []

    # If apply clicked OR we are looking at specific tabs, load data immediately
    if show_results:
        scores = scores.order_by(*ordering_map.get(sort, ("-total_score", "-resume__received_at")))
        if selected_job:
            criteria_map = {c.id: c for c in selected_job.criteria.all()}
        
        paginator = Paginator(scores, page_size)
        page_obj = paginator.get_page(page)
        visible_scores = list(page_obj.object_list)

        # Hydrate text/urls
        uploader = GoogleStorageUploader()
        for score in visible_scores:
            resume = score.resume
            signed = None
            if resume.file_url:
                if resume.file_url.startswith("http"):
                    signed = resume.file_url
                else:
                    signed = uploader.signed_url_for_blob(resume.file_url)
            resume.signed_url = signed
            # Populate text if missing (optional lazy load)
            if not resume.text_content:
                populate_resume_text(resume, uploader)

    # Chat Session Logic (Unchanged)
    chat_session = None
    chat_messages = []
    session_key = request.session.get("chat_session_key")
    if session_key:
        chat_session = ChatSession.objects.filter(session_key=session_key, user=request.user).first()
    if not chat_session:
        chat_session = ChatSession.objects.create(
            user=request.user,
            session_key=uuid.uuid4().hex,
            metadata={"job_id": selected_job.id if selected_job else None},
        )
        request.session["chat_session_key"] = chat_session.session_key
    elif selected_job and chat_session.metadata.get("job_id") != selected_job.id:
        metadata = chat_session.metadata or {}
        metadata["job_id"] = selected_job.id
        chat_session.metadata = metadata
        chat_session.save(update_fields=["metadata", "updated_at"])
    chat_messages = list(chat_session.messages.order_by("created_at")[:20])

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        template_name = "jobs/partials/score_cards.html" if view_mode == "tiles" else "jobs/partials/score_rows.html"
        html = render_to_string(
            template_name,
            {
                "scores": visible_scores,
                "selected_job": selected_job,
                "criteria_map": criteria_map,
                "view_mode": view_mode,
            },
            request=request,
        )
        return JsonResponse(
            {
                "html": html,
                "has_next": page_obj.has_next() if show_results else False,
                "next_page": page_obj.next_page_number() if show_results and page_obj.has_next() else None,
                "added": len(visible_scores),
                "end_index": page_obj.end_index() if show_results else 0,
                "total_count": paginator.count if show_results else 0,
            }
        )
        
    return render(
        request,
        "jobs/dashboard.html",
        {
            "jobs": jobs,
            "selected_job": selected_job,
            "scores": visible_scores,
            "criteria_map": criteria_map,
            "sort": sort,
            "view_mode": view_mode,
            "page_obj": page_obj,
            "total_count": paginator.count if show_results else 0,
            "page_size": page_size,
            "chat_session": chat_session,
            "chat_messages": chat_messages,
            "show_results": show_results,
            "current_tab": current_tab, # Pass tab to UI so we can highlight the active button
        },
    )


@login_required
def upload_resumes(request):
    form = ResumeUploadForm(request.POST or None, request.FILES or None)
    ingest_result = None

    def respond(payload, status=200):
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(payload, status=status)
        if payload.get("success"):
            messages.success(request, payload.get("message", "Upload complete."))
        else:
            messages.error(request, payload.get("message", "Upload failed."))
        return redirect(request.META.get("HTTP_REFERER", reverse("upload_resumes")))

    if request.method == "POST":
        job_id = request.POST.get("job")
        if not job_id:
            return respond({"success": False, "message": "Please select a JD."}, status=400)
        try:
            job = JobDescription.objects.get(pk=job_id)
        except JobDescription.DoesNotExist:
            return respond({"success": False, "message": "Selected JD not found."}, status=400)

        files = request.FILES.getlist("files")
        if not files:
            return respond({"success": False, "message": "No file selected. Please choose at least one resume to upload."}, status=400)

        ingestor = ResumeIngestor()
        attachments = []
        now = timezone.now()
        for file_obj in files:
            try:
                content_bytes = file_obj.read()
                attachments.append(
                    AttachmentPayload(
                        message_id=f"upload-{uuid.uuid4().hex}",
                        sender=None,
                        attachment_name=file_obj.name,
                        received_at=now,
                        content=content_bytes,
                        source="upload",
                    )
                )
            except Exception as exc:
                logger.exception("Failed to read uploaded resume", extra={"name": getattr(file_obj, "name", "")})
                return respond({"success": False, "message": f"Could not read {getattr(file_obj, 'name', 'file')}: {exc}"}, status=400)

        if attachments:
            try:
                saved = ingestor.ingest(attachments, jobs=[job])
                ingest_result = saved
                new_scores = list(
                    ResumeScore.objects.select_related("resume", "job").filter(
                        resume__message_id__in=[a.message_id for a in attachments],
                        job=job,
                    )
                )
                uploader = GoogleStorageUploader()
                for score in new_scores:
                    resume = score.resume
                    signed = None
                    if resume.file_url:
                        if resume.file_url.startswith("http"):
                            signed = resume.file_url
                        else:
                            signed = uploader.signed_url_for_blob(resume.file_url)
                    resume.signed_url = signed
                cards_html = render_to_string(
                    "jobs/partials/score_cards.html",
                    {"scores": new_scores, "selected_job": job, "criteria_map": {}, "view_mode": "tiles"},
                    request=request,
                )
                rows_html = render_to_string(
                    "jobs/partials/score_rows.html",
                    {"scores": new_scores, "selected_job": job, "criteria_map": {}, "view_mode": "table"},
                    request=request,
                )
                payload = {
                    "success": True,
                    "message": f"Uploaded and scored {saved} resume(s).",
                    "count": saved,
                    "job_id": job.id,
                    "job_name": job.name,
                    "cards_html": cards_html,
                    "rows_html": rows_html,
                }
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse(payload)
                messages.success(request, payload["message"])
            except Exception as exc:
                logger.exception("Upload ingest failed", extra={"error": str(exc)})
                return respond({"success": False, "message": f"Upload failed: {exc}"}, status=503)
        else:
            return respond({"success": False, "message": "No files were processed."}, status=400)
    return render(
        request,
        "jobs/upload.html",
        {"form": form, "ingest_result": ingest_result},
    )


@login_required
def toggle_resume_flag(request, resume_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    field = request.POST.get("field")
    value_raw = request.POST.get("value")
    if field not in ("is_favorite",):
        return HttpResponseBadRequest("Unsupported flag")
    resume = get_object_or_404(Resume, pk=resume_id)
    value = str(value_raw).lower() in ("true", "1", "yes", "on")
    setattr(resume, field, value)
    resume.save(update_fields=[field])
    payload = {"success": True, "field": field, "value": value}
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(payload)
    messages.success(request, f"Updated {field.replace('_', ' ')} for {resume.attachment_name}.")
    return redirect(request.META.get("HTTP_REFERER", reverse("dashboard")))


def _get_or_create_chat_session(request, job_id=None) -> ChatSession:
    session_key = request.session.get("chat_session_key")
    session = None
    if session_key:
        session = ChatSession.objects.filter(session_key=session_key, user=request.user).first()
    if not session:
        session = ChatSession.objects.create(
            user=request.user,
            session_key=uuid.uuid4().hex,
            metadata={"job_id": job_id},
        )
        request.session["chat_session_key"] = session.session_key
    return session


@login_required
def chat_search(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    message = (payload.get("message") or "").strip()
    job_id = payload.get("job_id") or None
    if not message:
        return HttpResponseBadRequest("Message required")

    session = _get_or_create_chat_session(request, job_id)
    if job_id:
        metadata = session.metadata or {}
        metadata["job_id"] = job_id
        session.metadata = metadata
        session.save(update_fields=["metadata", "updated_at"])

    ChatMessage.objects.create(session=session, role="user", content=message)

    view_mode = payload.get("view") or "tiles"
    scores = ResumeScore.objects.select_related("resume", "job")
    selected_job = None
    if job_id:
        selected_job = JobDescription.objects.filter(pk=job_id).first()
        scores = scores.filter(job_id=job_id)
    else:
        scores = scores.filter(job__active=True)

    tokens = [t for t in re.split(r"\s+", message) if len(t) > 2]
    if tokens:
        query = Q()
        for token in tokens:
            query |= Q(resume__text_content__icontains=token)
            query |= Q(resume__candidate_name__icontains=token)
            query |= Q(resume__candidate_email__icontains=token)
            query |= Q(resume__attachment_name__icontains=token)
        scores = scores.filter(query)

    scores = list(scores.order_by("-total_score", "-resume__received_at")[:50])
    uploader = GoogleStorageUploader()
    for score in scores:
        resume = score.resume
        signed = None
        if resume.file_url:
            if resume.file_url.startswith("http"):
                signed = resume.file_url
            else:
                signed = uploader.signed_url_for_blob(resume.file_url)
        resume.signed_url = signed
        if not resume.text_content:
            populate_resume_text(resume, uploader)
    template_name = "jobs/partials/score_cards.html" if view_mode == "tiles" else "jobs/partials/score_rows.html"
    html = render_to_string(
        template_name,
        {
            "scores": scores,
            "selected_job": selected_job,
            "criteria_map": {},
            "view_mode": view_mode,
        },
        request=request,
    )

    reply = f"Found {len(scores)} candidate(s) for \"{message}\""
    if job_id:
        try:
            job = JobDescription.objects.get(pk=job_id)
            reply += f" in {job.name}"
        except JobDescription.DoesNotExist:
            pass
    ChatMessage.objects.create(session=session, role="assistant", content=reply)

    return JsonResponse({"session_id": session.session_key, "reply": reply, "html": html, "count": len(scores)})


@login_required
def error_log(request):
    logs = ErrorLog.objects.select_related("resume").order_by("-created_at")[:200]
    return render(request, "jobs/error_log.html", {"logs": logs})


@login_required
def resolve_error(request, log_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")
    log = get_object_or_404(ErrorLog, pk=log_id)
    log.resolved = True
    log.save(update_fields=["resolved"])
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"success": True})
    messages.success(request, f"Marked error {log.id} as resolved.")
    return redirect(reverse("error_log"))


@require_POST
def schedule_interview(request, score_id):
    score = get_object_or_404(ResumeScore, pk=score_id)
    resume = score.resume
    
    # Get form data
    date_str = request.POST.get('interview_date') # Format: YYYY-MM-DDTHH:MM
    link = request.POST.get('interview_link')
    notes = request.POST.get('message_notes')

    if date_str:
        resume.interview_date = parse_datetime(date_str)
    
    resume.interview_link = link
    resume.status = 'INTERVIEW_SCHEDULED'
    resume.save()

    # Send Email
    if resume.candidate_email:
        subject = f"Interview Invitation: {score.job.name} at Minfy"
        message = (
            f"Dear {resume.candidate_name},\n\n"
            f"We reviewed your application for {score.job.name} and would like to invite you for an interview.\n\n"
            f"Time: {date_str.replace('T', ' ')}\n"
            f"Location/Link: {link}\n\n"
            f"Notes: {notes}\n\n"
            "Please reply to confirm your availability.\n\n"
            "Best,\nMinfy HR Team"
        )
        try:
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [resume.candidate_email])
            resume.interview_email_sent = True
            resume.save()
            messages.success(request, f"Interview invite sent to {resume.candidate_name}")
        except Exception as e:
            messages.error(request, f"Failed to send email: {e}")
    
    return redirect('dashboard')

# jobs/views.py

@require_POST
@login_required
def update_status(request, score_id):
    """
    Manually update status (e.g., Hire vs Reject after interview).
    """
    score = get_object_or_404(ResumeScore, pk=score_id)
    resume = score.resume
    action = request.POST.get('action')

    if action == 'hire':
        resume.status = 'HIRED'
        messages.success(request, f"🎉 {resume.candidate_name} marked as HIRED!")
    elif action == 'reject':
        resume.status = 'INTERVIEW_REJECTED' # Specific status for post-interview rejection
        messages.info(request, f"{resume.candidate_name} marked as Rejected.")
    
    resume.save()
    
    # Refresh the page (stay on current tab)
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))



# jobs/views.py
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from .models import Resume

@require_POST
def update_human_score(request, pk):
    resume = get_object_or_404(Resume, pk=pk)
    
    try:
        data = json.loads(request.body)
        breakdown = data.get('breakdown', {}) # {'Python': 80, 'Communication': 90}
        
        # Calculate Aggregate Average
        if breakdown:
            # Convert values to floats and filter out empty ones
            scores = []
            for k, v in breakdown.items():
                try:
                    scores.append(float(v))
                except (ValueError, TypeError):
                    continue
            
            if scores:
                total_avg = sum(scores) / len(scores)
                resume.human_score = round(total_avg, 1)
                resume.human_score_breakdown = breakdown
                resume.save()
                return JsonResponse({'status': 'success'})
        
        return JsonResponse({'status': 'error', 'message': 'No valid scores provided'})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)