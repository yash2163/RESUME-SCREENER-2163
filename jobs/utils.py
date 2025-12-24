# jobs/utils.py
from .models import ResumeActivityLog

def log_activity(resume, action_type, details="", user=None):
    """
    Creates a log entry for a resume.
    Safe to call from anywhere (views, services, tasks).
    """
    try:
        ResumeActivityLog.objects.create(
            resume=resume,
            action_type=action_type,
            details=details,
            user=user
        )
    except Exception as e:
        print(f"Failed to log activity: {e}")