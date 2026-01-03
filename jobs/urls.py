from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("export/", views.export_scores_csv, name="export_scores_csv"),
    path("upload/", views.upload_resumes, name="upload_resumes"),
    path("chat/search/", views.chat_search, name="chat_search"),
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/new/", views.job_create, name="job_create"),
    path("jobs/<int:pk>/edit/", views.job_edit, name="job_edit"),
    path("jobs/<int:pk>/toggle/", views.toggle_job_active, name="job_toggle"),
    path("jobs/<int:pk>/rescore/", views.rescore_job, name="job_rescore"),
    path("scores/<int:score_id>/rescore/", views.rescore_single_score, name="score_rescore"),
    path("resumes/<int:resume_id>/toggle/", views.toggle_resume_flag, name="resume_toggle"),
    path("errors/", views.error_log, name="error_log"),
    path("errors/<int:log_id>/resolve/", views.resolve_error, name="resolve_error"),
    path('schedule/<int:score_id>/', views.schedule_interview, name='schedule_interview'),
    path('status/<int:score_id>/', views.update_status, name='update_status'),
    path('shortlist/<int:pk>/', views.shortlist_candidate, name='shortlist_candidate'), # New
    path('rate/<int:pk>/', views.update_human_score, name='update_human_score'),       # New
    path('rate-human/<int:pk>/', views.update_human_score, name='update_human_score'),
    # path("resumes/<int:pk>/", views.resume_detail, name="view_resume"),
]
