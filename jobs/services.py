# import base64
# import json
# import logging
# import re
# import threading
# from concurrent.futures import ThreadPoolExecutor, as_completed
# from dataclasses import dataclass
# from io import BytesIO
# from datetime import datetime, timedelta
# from typing import Iterable, List, Optional, Tuple

# import google.generativeai as genai  # kept for compatibility if API key provided
# from google.cloud import storage
# from google.api_core.retry import Retry
# from google.cloud import aiplatform
# from vertexai import generative_models as vertex_models
# import msal
# import requests
# from django.conf import settings
# from django.db import close_old_connections
# from django.utils import timezone
# from pypdf import PdfReader
# from docx import Document

# from django.core.mail import send_mail

# from .models import ErrorLog, JobDescription, Resume, ResumeScore, ResumeSource

# logger = logging.getLogger(__name__)


# def record_error(location: str, message: str, details: str = "", context: Optional[dict] = None, resume: Optional[Resume] = None) -> None:
#     try:
#         ErrorLog.objects.create(
#             location=location,
#             message=truncate(message, 500) if "truncate" in globals() else message[:500],
#             details=truncate(details, 2000) if "truncate" in globals() else details[:2000],
#             context=context or {},
#             resume=resume,
#         )
#     except Exception:
#         logger.exception("Failed to persist error log", extra={"location": location})


# def extract_text_from_pdf(content: bytes) -> str:
#     reader = PdfReader(BytesIO(content))
#     text_parts: List[str] = []
#     for page in reader.pages:
#         text_parts.append(page.extract_text() or "")
#     return "\n".join(text_parts)


# def extract_text_from_docx(content: bytes) -> str:
#     document = Document(BytesIO(content))
#     parts = [paragraph.text for paragraph in document.paragraphs]
#     return "\n".join(parts)


# def extract_text_from_attachment(name: str, content_bytes: bytes) -> str:
#     if name.lower().endswith(".pdf"):
#         return extract_text_from_pdf(content_bytes)
#     if name.lower().endswith((".docx", ".doc")):
#         try:
#             return extract_text_from_docx(content_bytes)
#         except Exception:
#             # Fall through to text decode if the DOC/DOCX parsing fails
#             pass
#     try:
#         return content_bytes.decode("utf-8", errors="ignore")
#     except Exception:
#         return ""


# def clean_string(value: str) -> str:
#     # Remove NULs and trim whitespace to avoid DB issues
#     return value.replace("\x00", "").strip()


# def truncate(value: str, length: int = 2000) -> str:
#     if not value:
#         return value
#     return value[:length]


# def extract_email_from_text(text: str) -> Optional[str]:
#     match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
#     return match.group(0) if match else None


# class GeminiEvaluator:
#     def __init__(self, api_key: Optional[str] = None):
#         self.api_key = api_key or settings.GEMINI_API_KEY
#         self.vertex_project = settings.VERTEX_PROJECT_ID
#         self.vertex_location = settings.VERTEX_LOCATION
#         self.vertex_model_name = settings.VERTEX_MODEL or "gemini-1.5-pro"
#         self.model = None

#         if self.api_key:
#             genai.configure(api_key=self.api_key)
#             # self.model = genai.GenerativeModel("gemini-2.5-pro")
#             self.model = genai.GenerativeModel("gemini-2.5-flash-lite")
#             logger.info("Using Gemini API key authentication.")
#         elif self.vertex_project and self.vertex_location:
#             try:
#                 aiplatform.init(project=self.vertex_project, location=self.vertex_location)
#                 self.model = vertex_models.GenerativeModel(self.vertex_model_name)
#                 logger.info(
#                     "Using Vertex AI service account auth.",
#                     extra={"project": self.vertex_project, "location": self.vertex_location, "model": self.vertex_model_name},
#                 )
#             except Exception as exc:
#                 logger.exception("Vertex AI init failed; falling back to keyword scoring", extra={"error": str(exc)})
#         else:
#             logger.warning("No Gemini API key or Vertex config; using fallback keyword scoring.")

#     def _extract_text(self, response) -> str:
#         # Try to pull raw text from response or first candidate parts
#         if hasattr(response, "text") and response.text:
#             return response.text
#         try:
#             parts = []
#             candidate = response.candidates[0]
#             content = getattr(candidate, "content", None)
#             if content and hasattr(content, "parts"):
#                 for part in content.parts:
#                     if hasattr(part, "text") and part.text:
#                         parts.append(part.text)
#             return "\n".join(parts)
#         except Exception:
#             return ""

#     def _parse_json(self, raw: str) -> dict:
#         if not raw:
#             return {}
#         raw = raw.strip()
#         # Remove code fences if present
#         if raw.startswith("```"):
#             raw = raw.strip("`")
#             if raw.lower().startswith("json"):
#                 raw = raw[4:]
#         try:
#             return json.loads(raw)
#         except Exception:
#             # Try to extract the first JSON object
#             import re
#             match = re.search(r"\{.*\}", raw, re.DOTALL)
#             if match:
#                 try:
#                     return json.loads(match.group(0))
#                 except Exception:
#                     return {}
#         return {}

#     def score_resume_against_job(self, resume_text: str, job: JobDescription) -> Tuple[float, list]:
#         criteria = list(job.criteria.all())
#         if not criteria:
#             return 0.0, []

#         if not self.model:
#             return self._keyword_score(resume_text, criteria)

#         prompt = (
#             "You are an HR specialist screening a resume. Do not rewrite or alter the provided criteria text. "
#             "For each criterion, return a score 0-100 based only on evidence in the resume and a brief note if the score is low. "
#             "Compute total_score as the average of all criterion scores. Respond with JSON ONLY (no prose, no code fences) "
#             "in this shape: {\"total_score\": number, \"criteria\": [{\"criterion_id\": id, \"title\": \"criterion text exactly as provided\", \"score\": number, \"notes\": \"short why\"}]}"
#         )
#         criteria_payload = [{"id": c.id, "detail": c.detail} for c in criteria]
#         clipped_resume = resume_text[:6000]
#         content = [
#             prompt,
#             f"Job: {job.name}\nSummary: {job.summary}\nCriteria: {json.dumps(criteria_payload)}\nResume:\n{clipped_resume}",
#         ]
#         try:
#             logger.info("Gemini scoring start", extra={"job": job.id, "resume_len": len(clipped_resume)})
#             response = self.model.generate_content(content) if hasattr(self.model, "generate_content") else self.model.generate_content(
#                 contents=content
#             )
#             raw = self._extract_text(response)
#             parsed = self._parse_json(raw)
#             detail = parsed.get("criteria", [])
#             total = float(parsed.get("total_score", 0))
#             logger.info(
#                 "Gemini scoring success",
#                 extra={"job": job.id, "criteria_count": len(detail), "total_score": total},
#             )
#             return total, detail
#         except Exception as exc:
#             logger.exception("Gemini scoring failed, falling back", extra={"job": job.id, "error": str(exc)})
#             return self._keyword_score(resume_text, criteria)

#     def _keyword_score(self, resume_text: str, criteria: Iterable) -> Tuple[float, list]:
#         resume_lower = resume_text.lower()
#         detail = []
#         total = 0.0
#         for c in criteria:
#             tokens = re.split(r"[,\n;/]+", c.detail)
#             tokens = [t.strip().lower() for t in tokens if len(t.strip()) > 3]
#             token_hits = sum(1 for t in tokens if t and t in resume_lower)
#             score = 0.0
#             if tokens:
#                 score = (token_hits / len(tokens)) * 100.0
#             detail.append(
#                 {
#                     "criterion_id": c.id,
#                     "title": c.detail,
#                     "score": score,
#                     "notes": "Fallback keyword match",
#                 }
#             )
#             total += score
#         if criteria:
#             total = total / len(criteria)
#         return total, detail


# @dataclass
# class AttachmentPayload:
#     message_id: str
#     sender: Optional[str]
#     attachment_name: str
#     received_at: timezone.datetime
#     content: bytes
#     source: str = ResumeSource.EMAIL


# def extract_phone_number(text: str) -> str:
#     # Basic phone matcher allowing +, digits, spaces, dashes, parentheses
#     match = re.search(r"\+?\d[\d\s\-\(\)]{6,}", text)
#     return match.group(0).strip() if match else ""


# def infer_active_seeker(sender: Optional[str], candidate_email: Optional[str], source: str) -> bool:
#     sender_l = (sender or "").lower()
#     email_l = (candidate_email or "").lower()
#     if source == ResumeSource.UPLOAD:
#         return True
#     if "linkedin" in sender_l:
#         return True
#     if sender_l and email_l and sender_l == email_l:
#         return True
#     return False


# class GoogleStorageUploader:
#     def __init__(self, bucket_name: Optional[str] = None):
#         self.bucket_name = bucket_name or settings.GCS_BUCKET_NAME
#         self.client = None
#         if self.bucket_name:
#             self.client = storage.Client()

#     def upload(self, name: str, content: bytes, content_type: Optional[str] = None) -> Optional[str]:
#         if not self.client or not self.bucket_name:
#             return None
#         bucket = self.client.bucket(self.bucket_name)
#         blob = bucket.blob(name)
#         try:
#             blob.upload_from_string(content, content_type=content_type, timeout=120, retry=Retry(deadline=180))
#             # Store the blob path; signed URLs are generated on demand for display/download
#             return blob.name
#         except Exception as exc:
#             logger.exception("GCS upload failed", extra={"blob": name, "error": str(exc)})
#             record_error("gcs_upload", "GCS upload failed", details=str(exc), context={"blob": name})
#             return None

#     def signed_url_for_blob(self, blob_name: str) -> Optional[str]:
#         if not blob_name:
#             return None
#         if not self.bucket_name:
#             return None
#         # If client unavailable (e.g., missing credentials), return a best-effort public URL
#         if not self.client:
#             return f"https://storage.googleapis.com/{self.bucket_name}/{blob_name}"
#         bucket = self.client.bucket(self.bucket_name)
#         blob = bucket.blob(blob_name)
#         try:
#             return blob.generate_signed_url(
#                 version="v4",
#                 expiration=timedelta(seconds=getattr(settings, "GCS_SIGNED_URL_TTL_SECONDS", 604800)),
#                 method="GET",
#                 response_disposition="inline",
#             )
#         except Exception:
#             return blob.public_url or f"https://storage.googleapis.com/{self.bucket_name}/{blob_name}"

#     def download_blob(self, blob_name: str) -> Optional[bytes]:
#         if not self.client or not self.bucket_name or not blob_name:
#             return None
#         try:
#             bucket = self.client.bucket(self.bucket_name)
#             blob = bucket.blob(blob_name)
#             return blob.download_as_bytes()
#         except Exception as exc:
#             logger.exception("Blob download failed", extra={"blob": blob_name, "error": str(exc)})
#             return None


# class M365InboxReader:
#     def __init__(self, config=None):
#         self.config = config or settings.M365_CONFIG

#     def is_configured(self, log: bool = True) -> bool:
#         required = ["tenant_id", "client_id", "client_secret"]
#         missing = [key for key in required if not self.config.get(key)]
#         if missing and log:
#             logger.warning("M365 configuration missing keys", extra={"missing_keys": missing})
#             return False
#         return True

#     def _client(self):
#         if not self.is_configured(log=False):
#             return None
#         authority = f"https://login.microsoftonline.com/{self.config.get('tenant_id')}"
#         return msal.ConfidentialClientApplication(
#             self.config.get("client_id"),
#             authority=authority,
#             client_credential=self.config.get("client_secret"),
#         )

#     def _fetch_attachments_for_message(self, message: dict, headers: dict, user: str) -> List[AttachmentPayload]:
#         """Fetch attachments for a single message; intended for thread pool use."""
#         message_id = message["id"]
#         sender = (message.get("from") or {}).get("emailAddress", {}).get("address")
#         received_at = timezone.make_aware(datetime.strptime(message["receivedDateTime"], "%Y-%m-%dT%H:%M:%SZ"))
#         attach_url = f"https://graph.microsoft.com/v1.0/users/{user}/messages/{message_id}/attachments"
#         try:
#             attach_raw = requests.get(attach_url, headers=headers, timeout=30)
#             attach_resp = attach_raw.json()
#             if attach_raw.status_code >= 400 or attach_resp.get("error"):
#                 logger.error(
#                     "Attachment fetch failed",
#                     extra={
#                         "message_id": message_id,
#                         "status": attach_raw.status_code,
#                         "error": attach_resp.get("error"),
#                     },
#                 )
#                 return []
#         except Exception as exc:
#             logger.exception("Attachment fetch failed", extra={"message_id": message_id, "error": str(exc)})
#             return []

#         found: List[AttachmentPayload] = []
#         for attachment in attach_resp.get("value", []):
#             if not attachment.get("contentBytes"):
#                 continue
#             name = attachment.get("name", "attachment")
#             if not name.lower().endswith((".pdf", ".txt", ".docx", ".doc")):
#                 continue
#             try:
#                 content = base64.b64decode(attachment["contentBytes"])
#             except Exception:
#                 continue
#             found.append(
#                 AttachmentPayload(
#                     message_id=message_id,
#                     sender=sender,
#                     attachment_name=name,
#                     received_at=received_at,
#                     content=content,
#                     source=ResumeSource.EMAIL,
#                 )
#             )
#         return found

#     def fetch_recent_attachments(self, limit: int = 5, fetch_all: bool = False) -> List[AttachmentPayload]:
#         client = self._client()
#         if not client:
#             return []

#         token = client.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
#         if "access_token" not in token:
#             logger.error(
#                 "M365 token acquisition failed",
#                 extra={
#                     "error": token.get("error"),
#                     "error_description": token.get("error_description"),
#                     "correlation_id": token.get("correlation_id"),
#                 },
#             )
#             return []

#         headers = {"Authorization": f"Bearer {token['access_token']}"}
#         user = self.config.get("user_id", "me")
#         top = min(limit, 100) if limit else 50
#         base_url = f"https://graph.microsoft.com/v1.0/users/{user}/messages"
#         # Primary query filters to attachments; fallback retries without filter if the mailbox rejects it as inefficient
#         messages_url = f"{base_url}?$filter=hasAttachments eq true&$orderby=receivedDateTime desc&$top={top}"
#         fallback_url = f"{base_url}?$orderby=receivedDateTime desc&$top={top}"
#         messages = []
#         next_url = messages_url
#         attempted_fallback = False
#         while next_url:
#             resp_raw = requests.get(next_url, headers=headers, timeout=30)
#             resp = resp_raw.json()
#             if resp_raw.status_code >= 400 or resp.get("error"):
#                 error_code = (resp.get("error") or {}).get("code")
#                 if not attempted_fallback and error_code == "InefficientFilter":
#                     logger.warning(
#                         "M365 message fetch inefficient filter; retrying without hasAttachments filter",
#                         extra={"url": next_url},
#                     )
#                     next_url = fallback_url
#                     attempted_fallback = True
#                     continue
#                 logger.error(
#                     "M365 message fetch failed",
#                     extra={
#                         "status": resp_raw.status_code,
#                         "error": resp.get("error"),
#                         "url": next_url,
#                     },
#                 )
#                 break
#             messages.extend(resp.get("value", []))
#             if not fetch_all and len(messages) >= limit:
#                 messages = messages[:limit]
#                 break
#             next_url = resp.get("@odata.nextLink") if fetch_all else None

#         attachments: List[AttachmentPayload] = []
#         if not messages:
#             return attachments

#         max_workers = min(8, len(messages))
#         with ThreadPoolExecutor(max_workers=max_workers) as executor:
#             future_map = {
#                 executor.submit(self._fetch_attachments_for_message, message, headers, user): message.get("id")
#                 for message in messages
#             }
#             for future in as_completed(future_map):
#                 try:
#                     attachments.extend(future.result() or [])
#                 except Exception as exc:
#                     logger.exception("Attachment worker failed", extra={"message_id": future_map[future], "error": str(exc)})
#         return attachments


# class ResumeIngestor:
#     def __init__(
#         self,
#         scorer: Optional[GeminiEvaluator] = None,
#         uploader: Optional[GoogleStorageUploader] = None,
#         max_workers: Optional[int] = None,
#         score_workers: Optional[int] = None,
#     ):
#         self.scorer = scorer  # Lazily resolved per thread to avoid cross-thread client reuse issues
#         self.uploader = uploader or GoogleStorageUploader()
#         self.max_workers = max_workers or getattr(settings, "INGEST_MAX_WORKERS", 4)
#         self.score_workers = score_workers or getattr(settings, "INGEST_SCORE_WORKERS", self.max_workers)
#         self._scorer_local = threading.local()

#     def _get_scorer(self) -> GeminiEvaluator:
#         """Thread-local scorer to keep Gemini client usage safe under concurrency."""
#         if not getattr(self._scorer_local, "instance", None):
#             self._scorer_local.instance = self.scorer or GeminiEvaluator()
#         return self._scorer_local.instance

#     # def _score_resume_for_jobs(self, resume: Resume, text_content: str, jobs: List[JobDescription]) -> None:
#     #     if not jobs:
#     #         return
#     #     max_workers = max(1, min(self.score_workers, len(jobs)))

#     #     def score_job(job: JobDescription):
#     #         close_old_connections()
#     #         scorer = self._get_scorer()
#     #         try:
#     #             total, detail = scorer.score_resume_against_job(text_content, job)
#     #             return job, total, detail
#     #         finally:
#     #             close_old_connections()

#     #     results = []
#     #     with ThreadPoolExecutor(max_workers=max_workers) as executor:
#     #         future_map = {executor.submit(score_job, job): job for job in jobs}
#     #         for future in as_completed(future_map):
#     #             try:
#     #                 job, total, detail = future.result()
#     #                 results.append((job, total, detail))
#     #             except Exception as exc:
#     #                 logger.exception("Scoring failed", extra={"job_id": getattr(future_map[future], "id", None), "error": str(exc)})
#     #                 record_error(
#     #                     location="ingest:score_job",
#     #                     message="Resume scoring failed",
#     #                     details=str(exc),
#     #                     context={"job_id": getattr(future_map[future], "id", None), "resume_id": getattr(resume, "id", None)},
#     #                 )

#     def _score_resume_for_jobs(self, resume: Resume, text_content: str, jobs: List[JobDescription]) -> None:
#         if not jobs:
#             return
#         max_workers = max(1, min(self.score_workers, len(jobs)))

#         def score_job(job: JobDescription):
#             close_old_connections()
#             scorer = self._get_scorer()
#             try:
#                 total, detail = scorer.score_resume_against_job(text_content, job)
#                 return job, total, detail
#             finally:
#                 close_old_connections()

#         results = []
#         with ThreadPoolExecutor(max_workers=max_workers) as executor:
#             future_map = {executor.submit(score_job, job): job for job in jobs}
#             for future in as_completed(future_map):
#                 try:
#                     job, total, detail = future.result()
#                     results.append((job, total, detail))
#                 except Exception as exc:
#                     logger.exception("Scoring failed", extra={"job_id": getattr(future_map[future], "id", None), "error": str(exc)})
#                     record_error(
#                         location="ingest:score_job",
#                         message="Resume scoring failed",
#                         details=str(exc),
#                         context={"job_id": getattr(future_map[future], "id", None), "resume_id": getattr(resume, "id", None)},
#                     )

#         for job, total, detail in results:
#             # 1. Create/Update the score object
#             score_obj, created = ResumeScore.objects.update_or_create(
#                 resume=resume,
#                 job=job,
#                 defaults={"total_score": total, "detail": detail},
#             )
            
#             # 2. TRIGGER AUTOMATION (Auto-Reject / Shortlist)
#             # This calls the function we added to the bottom of the file earlier
#             try:
#                 check_and_process_automation(score_obj)
#             except Exception as e:
#                 logger.error(f"Automation trigger failed for resume {resume.id}: {e}")

#         for job, total, detail in results:
#             ResumeScore.objects.update_or_create(
#                 resume=resume,
#                 job=job,
#                 defaults={"total_score": total, "detail": detail},
#             )

#     def _record_failure(self, attachment: AttachmentPayload, jobs: List[JobDescription], error: Exception) -> None:
#         """Persist a failure placeholder so it shows on the dashboard."""
#         try:
#             sender_clean = clean_string(attachment.sender or "") if attachment.sender else None
#             candidate_name = clean_string(attachment.attachment_name.rsplit(".", 1)[0])[:255]
#             resume, _ = Resume.objects.get_or_create(
#                 message_id=attachment.message_id,
#                 attachment_name=attachment.attachment_name,
#                 defaults={
#                     "sender": sender_clean,
#                     "candidate_email": sender_clean,
#                     "candidate_phone": "",
#                     "candidate_name": candidate_name,
#                     "text_content": "",
#                     "file_url": "",
#                     "received_at": attachment.received_at,
#                     "source": attachment.source or ResumeSource.EMAIL,
#                     "is_active_seeker": infer_active_seeker(sender_clean, sender_clean, attachment.source),
#                 },
#             )
#             detail = [{"title": "Processing failed", "score": 0, "notes": truncate(str(error), 200)}]
#             for job in jobs:
#                 ResumeScore.objects.update_or_create(
#                     resume=resume,
#                     job=job,
#                     defaults={"total_score": 0.0, "detail": detail},
#                 )
#             record_error(
#                 location="ingest:_record_failure",
#                 message="Resume ingest failed",
#                 details=str(error),
#                 context={"message_id": attachment.message_id, "attachment": attachment.attachment_name},
#                 resume=resume,
#             )
#         except Exception as exc:
#             logger.exception(
#                 "Failed to record ingest failure",
#                 extra={"message_id": attachment.message_id, "attachment": attachment.attachment_name, "error": str(exc)},
#             )

#     def _ingest_single(self, attachment: AttachmentPayload, jobs: List[JobDescription]) -> int:
#         close_old_connections()
#         try:
#             raw_text = extract_text_from_attachment(attachment.attachment_name, attachment.content)
#             text_content = clean_string(raw_text)
#             phone = extract_phone_number(text_content)
#             text_email = extract_email_from_text(text_content)
#             storage_path = f"resumes/{attachment.message_id}/{attachment.attachment_name}"
#             content_type = "application/pdf" if attachment.attachment_name.lower().endswith(".pdf") else "text/plain"
#             file_url = truncate(
#                 self.uploader.upload(storage_path, attachment.content, content_type=content_type) or "",
#                 2000,
#             )
#             candidate_name = clean_string(attachment.attachment_name.rsplit(".", 1)[0])[:255]
#             sender_clean = clean_string(attachment.sender or "") if attachment.sender else None
#             candidate_email = text_email or sender_clean
#             resume, created = Resume.objects.get_or_create(
#                 message_id=attachment.message_id,
#                 attachment_name=attachment.attachment_name,
#                 defaults={
#                     "sender": sender_clean,
#                     "candidate_email": candidate_email,
#                     "candidate_phone": phone,
#                     "candidate_name": candidate_name,
#                     "text_content": text_content,
#                     "file_url": file_url or "",
#                     "received_at": attachment.received_at,
#                     "source": attachment.source or ResumeSource.EMAIL,
#                     "is_active_seeker": infer_active_seeker(sender_clean, candidate_email, attachment.source),
#                 },
#             )
#             if not created:
#                 # If a previous attempt failed (empty text_content), refresh data; otherwise respect dedup
#                 if resume.text_content:
#                     return 0
#                 resume.sender = sender_clean
#                 resume.candidate_email = candidate_email
#                 resume.candidate_phone = phone
#                 resume.candidate_name = candidate_name
#                 resume.text_content = text_content
#                 resume.file_url = file_url or ""
#                 resume.received_at = attachment.received_at
#                 resume.source = attachment.source or ResumeSource.EMAIL
#                 resume.is_active_seeker = infer_active_seeker(sender_clean, candidate_email, attachment.source)
#                 resume.save(update_fields=["sender", "candidate_email", "candidate_phone", "candidate_name", "text_content", "file_url", "received_at", "source", "is_active_seeker"])

#             self._score_resume_for_jobs(resume, text_content, jobs)
#             return 1
#         except Exception as exc:
#             logger.exception(
#                 "Resume ingest failed",
#                 extra={"message_id": attachment.message_id, "attachment": attachment.attachment_name, "error": str(exc)},
#             )
#             self._record_failure(attachment, jobs, exc)
#             record_error(
#                 location="ingest:_ingest_single",
#                 message="Resume ingest failed",
#                 details=str(exc),
#                 context={"message_id": attachment.message_id, "attachment": attachment.attachment_name},
#             )
#             return 0
#         finally:
#             close_old_connections()

#     def ingest(self, attachments: List[AttachmentPayload], jobs: Optional[List[JobDescription]] = None) -> int:
#         if not attachments:
#             return 0

#         jobs = jobs or list(JobDescription.objects.filter(active=True))
#         max_workers = max(1, min(self.max_workers, len(attachments)))
#         saved_count = 0
#         with ThreadPoolExecutor(max_workers=max_workers) as executor:
#             futures = [executor.submit(self._ingest_single, attachment, jobs) for attachment in attachments]
#             for future in as_completed(futures):
#                 try:
#                     saved_count += future.result() or 0
#                 except Exception as exc:
#                     logger.exception("Ingest worker failed", extra={"error": str(exc)})
#         return saved_count


# def populate_resume_text(resume: Resume, uploader: Optional[GoogleStorageUploader] = None) -> bool:
#     """Attempt to hydrate missing resume text from stored files."""
#     if resume.text_content:
#         return False
#     uploader = uploader or GoogleStorageUploader()
#     content_bytes: Optional[bytes] = None
#     try:
#         if resume.file_url:
#             if resume.file_url.startswith("http"):
#                 resp = requests.get(resume.file_url, timeout=20)
#                 if resp.status_code < 400:
#                     content_bytes = resp.content
#             else:
#                 content_bytes = uploader.download_blob(resume.file_url)
#     except Exception as exc:
#         logger.exception("Resume text recovery failed", extra={"resume_id": resume.id, "error": str(exc)})
#         record_error(
#             location="populate_resume_text",
#             message="Resume text recovery failed",
#             details=str(exc),
#             context={"resume_id": resume.id},
#             resume=resume,
#         )
#         return False

#     if not content_bytes:
#         return False
#     text_content = clean_string(extract_text_from_attachment(resume.attachment_name, content_bytes))
#     if not text_content:
#         return False
#     resume.text_content = text_content
#     resume.save(update_fields=["text_content"])
#     return True


# def check_and_process_automation(resume_score):
#     """
#     Checks the score against the threshold.
#     - If Score < Threshold: Auto-reject and send email.
#     - If Score >= Threshold: Mark as Shortlisted.
#     """
#     resume = resume_score.resume
#     score_val = resume_score.total_score
#     # Default to 60 if not set in settings
#     threshold = getattr(settings, 'AUTO_REJECTION_THRESHOLD', 60) 

#     # 1. AUTO REJECTION LOGIC
#     if score_val < threshold:
#         # Update Status
#         resume.status = 'AUTO_REJECTED'
#         resume.save()

#         # Send Email (Only if not already sent)
#         if not resume.rejection_email_sent and resume.candidate_email:
#             subject = f"Update regarding your application at {resume_score.job.name}"
#             message = (
#                 f"Dear {resume.candidate_name or 'Candidate'},\n\n"
#                 f"Thank you for your interest in the {resume_score.job.name} position. "
#                 "We appreciate the time you took to apply.\n\n"
#                 "After reviewing your application, we have decided not to proceed with your candidacy "
#                 "at this time. We will keep your resume on file for future openings.\n\n"
#                 "Best regards,\n"
#                 "HR Team"
#             )
            
#             try:
#                 send_mail(
#                     subject,
#                     message,
#                     settings.DEFAULT_FROM_EMAIL,
#                     [resume.candidate_email],
#                     fail_silently=False,
#                 )
#                 # Mark flag as True so we don't spam them later
#                 resume.rejection_email_sent = True
#                 resume.save()
#                 print(f"✓ Rejection email sent to {resume.candidate_email}")
#             except Exception as e:
#                 print(f"✗ Failed to send email: {e}")
#                 record_error("automation:email", f"Failed to send rejection email to {resume.candidate_email}", str(e))

#     # 2. SHORTLIST LOGIC
#     else:
#         # Ensure status is NEW so it appears in the Inbox.
#         # If it was 'PENDING' during processing, move it to 'NEW'.
#         if resume.status != 'NEW':
#             resume.status = 'NEW'
#             resume.save()










import base64
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple

import google.generativeai as genai
from google.cloud import storage
from google.api_core.retry import Retry
from google.cloud import aiplatform
from vertexai import generative_models as vertex_models
import msal
import requests
from django.conf import settings
from django.db import close_old_connections
from django.db.models import Q  # <--- NEW IMPORT
from django.utils import timezone
from pypdf import PdfReader
from docx import Document
from.utils import log_activity

from django.core.mail import send_mail

from .models import ErrorLog, JobDescription, Resume, ResumeScore, ResumeSource

logger = logging.getLogger(__name__)


def record_error(location: str, message: str, details: str = "", context: Optional[dict] = None, resume: Optional[Resume] = None) -> None:
    try:
        ErrorLog.objects.create(
            location=location,
            message=truncate(message, 500) if "truncate" in globals() else message[:500],
            details=truncate(details, 2000) if "truncate" in globals() else details[:2000],
            context=context or {},
            resume=resume,
        )
    except Exception:
        logger.exception("Failed to persist error log", extra={"location": location})


def extract_text_from_pdf(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    text_parts: List[str] = []
    for page in reader.pages:
        text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def extract_text_from_docx(content: bytes) -> str:
    document = Document(BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    return "\n".join(parts)


def extract_text_from_attachment(name: str, content_bytes: bytes) -> str:
    if name.lower().endswith(".pdf"):
        return extract_text_from_pdf(content_bytes)
    if name.lower().endswith((".docx", ".doc")):
        try:
            return extract_text_from_docx(content_bytes)
        except Exception:
            pass
    try:
        return content_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def clean_string(value: str) -> str:
    return value.replace("\x00", "").strip()


def truncate(value: str, length: int = 2000) -> str:
    if not value:
        return value
    return value[:length]


def extract_email_from_text(text: str) -> Optional[str]:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match else None


class GeminiEvaluator:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.vertex_project = settings.VERTEX_PROJECT_ID
        self.vertex_location = settings.VERTEX_LOCATION
        self.vertex_model_name = settings.VERTEX_MODEL or "gemini-2.5-pro"
        self.model = None

        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel("gemini-2.5-flash-lite")
            logger.info("Using Gemini API key authentication.")
        elif self.vertex_project and self.vertex_location:
            try:
                aiplatform.init(project=self.vertex_project, location=self.vertex_location)
                self.model = vertex_models.GenerativeModel(self.vertex_model_name)
                logger.info("Using Vertex AI auth.")
            except Exception as exc:
                logger.exception("Vertex AI init failed")
        else:
            logger.warning("No Gemini config found.")

    def _extract_text(self, response) -> str:
        if hasattr(response, "text") and response.text:
            return response.text
        try:
            parts = []
            candidate = response.candidates[0]
            content = getattr(candidate, "content", None)
            if content and hasattr(content, "parts"):
                for part in content.parts:
                    if hasattr(part, "text") and part.text:
                        parts.append(part.text)
            return "\n".join(parts)
        except Exception:
            return ""

    def _parse_json(self, raw: str) -> dict:
        if not raw:
            return {}
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        try:
            return json.loads(raw)
        except Exception:
            import re
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    return {}
        return {}

    def score_resume_against_job(self, resume_text: str, job: JobDescription) -> Tuple[float, list]:
        criteria = list(job.criteria.all())
        if not criteria:
            return 0.0, []

        if not self.model:
            return self._keyword_score(resume_text, criteria)

        prompt = (
            "You are an HR specialist screening a resume. Do not rewrite criteria. "
            "For each criterion, return a score 0-100 based only on evidence. "
            "Respond with JSON ONLY: {\"total_score\": number, \"criteria\": [{\"criterion_id\": id, \"title\": \"text\", \"score\": number, \"notes\": \"short why\"}]}"
        )
        criteria_payload = [{"id": c.id, "detail": c.detail} for c in criteria]
        clipped_resume = resume_text[:6000]
        content = [
            prompt,
            f"Job: {job.name}\nSummary: {job.summary}\nCriteria: {json.dumps(criteria_payload)}\nResume:\n{clipped_resume}",
        ]
        try:
            response = self.model.generate_content(content) if hasattr(self.model, "generate_content") else self.model.generate_content(
                contents=content
            )
            raw = self._extract_text(response)
            parsed = self._parse_json(raw)
            detail = parsed.get("criteria", [])
            total = float(parsed.get("total_score", 0))
            return total, detail
        except Exception as exc:
            logger.exception("Gemini scoring failed", extra={"job": job.id, "error": str(exc)})
            return self._keyword_score(resume_text, criteria)

    def _keyword_score(self, resume_text: str, criteria: Iterable) -> Tuple[float, list]:
        resume_lower = resume_text.lower()
        detail = []
        total = 0.0
        for c in criteria:
            tokens = re.split(r"[,\n;/]+", c.detail)
            tokens = [t.strip().lower() for t in tokens if len(t.strip()) > 3]
            token_hits = sum(1 for t in tokens if t and t in resume_lower)
            score = 0.0
            if tokens:
                score = (token_hits / len(tokens)) * 100.0
            detail.append(
                {
                    "criterion_id": c.id,
                    "title": c.detail,
                    "score": score,
                    "notes": "Fallback keyword match",
                }
            )
            total += score
        if criteria:
            total = total / len(criteria)
        return total, detail


@dataclass
class AttachmentPayload:
    message_id: str
    sender: Optional[str]
    attachment_name: str
    received_at: timezone.datetime
    content: bytes
    source: str = ResumeSource.EMAIL


def extract_phone_number(text: str) -> str:
    match = re.search(r"\+?\d[\d\s\-\(\)]{6,}", text)
    return match.group(0).strip() if match else ""


def infer_active_seeker(sender: Optional[str], candidate_email: Optional[str], source: str) -> bool:
    sender_l = (sender or "").lower()
    email_l = (candidate_email or "").lower()
    if source == ResumeSource.UPLOAD:
        return True
    if "linkedin" in sender_l:
        return True
    if sender_l and email_l and sender_l == email_l:
        return True
    return False


class GoogleStorageUploader:
    def __init__(self, bucket_name: Optional[str] = None):
        self.bucket_name = bucket_name or settings.GCS_BUCKET_NAME
        self.client = None
        if self.bucket_name:
            self.client = storage.Client()

    def upload(self, name: str, content: bytes, content_type: Optional[str] = None) -> Optional[str]:
        if not self.client or not self.bucket_name:
            return None
        bucket = self.client.bucket(self.bucket_name)
        blob = bucket.blob(name)
        try:
            blob.upload_from_string(content, content_type=content_type, timeout=120, retry=Retry(deadline=180))
            return blob.name
        except Exception as exc:
            logger.exception("GCS upload failed", extra={"blob": name, "error": str(exc)})
            return None

    def signed_url_for_blob(self, blob_name: str) -> Optional[str]:
        if not blob_name:
            return None
        if not self.bucket_name:
            return None
        if not self.client:
            return f"[https://storage.googleapis.com/](https://storage.googleapis.com/){self.bucket_name}/{blob_name}"
        bucket = self.client.bucket(self.bucket_name)
        blob = bucket.blob(blob_name)
        try:
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=getattr(settings, "GCS_SIGNED_URL_TTL_SECONDS", 604800)),
                method="GET",
                response_disposition="inline",
            )
        except Exception:
            return blob.public_url or f"[https://storage.googleapis.com/](https://storage.googleapis.com/){self.bucket_name}/{blob_name}"

    def download_blob(self, blob_name: str) -> Optional[bytes]:
        if not self.client or not self.bucket_name or not blob_name:
            return None
        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(blob_name)
            return blob.download_as_bytes()
        except Exception as exc:
            return None


class M365InboxReader:
    def __init__(self, config=None):
        self.config = config or settings.M365_CONFIG

    def is_configured(self, log: bool = True) -> bool:
        required = ["tenant_id", "client_id", "client_secret"]
        missing = [key for key in required if not self.config.get(key)]
        if missing and log:
            return False
        return True

    def _client(self):
        if not self.is_configured(log=False):
            return None
        authority = f"[https://login.microsoftonline.com/](https://login.microsoftonline.com/){self.config.get('tenant_id')}"
        return msal.ConfidentialClientApplication(
            self.config.get("client_id"),
            authority=authority,
            client_credential=self.config.get("client_secret"),
        )

    def _fetch_attachments_for_message(self, message: dict, headers: dict, user: str) -> List[AttachmentPayload]:
        message_id = message["id"]
        sender = (message.get("from") or {}).get("emailAddress", {}).get("address")
        received_at = timezone.make_aware(datetime.strptime(message["receivedDateTime"], "%Y-%m-%dT%H:%M:%SZ"))
        attach_url = f"[https://graph.microsoft.com/v1.0/users/](https://graph.microsoft.com/v1.0/users/){user}/messages/{message_id}/attachments"
        try:
            attach_raw = requests.get(attach_url, headers=headers, timeout=30)
            attach_resp = attach_raw.json()
            if attach_raw.status_code >= 400:
                return []
        except Exception:
            return []

        found: List[AttachmentPayload] = []
        for attachment in attach_resp.get("value", []):
            if not attachment.get("contentBytes"):
                continue
            name = attachment.get("name", "attachment")
            if not name.lower().endswith((".pdf", ".txt", ".docx", ".doc")):
                continue
            try:
                content = base64.b64decode(attachment["contentBytes"])
            except Exception:
                continue
            found.append(
                AttachmentPayload(
                    message_id=message_id,
                    sender=sender,
                    attachment_name=name,
                    received_at=received_at,
                    content=content,
                    source=ResumeSource.EMAIL,
                )
            )
        return found

    def fetch_recent_attachments(self, limit: int = 5, fetch_all: bool = False) -> List[AttachmentPayload]:
        client = self._client()
        if not client:
            return []

        token = client.acquire_token_for_client(scopes=["[https://graph.microsoft.com/.default](https://graph.microsoft.com/.default)"])
        if "access_token" not in token:
            return []

        headers = {"Authorization": f"Bearer {token['access_token']}"}
        user = self.config.get("user_id", "me")
        top = min(limit, 100) if limit else 50
        base_url = f"[https://graph.microsoft.com/v1.0/users/](https://graph.microsoft.com/v1.0/users/){user}/messages"
        messages_url = f"{base_url}?$filter=hasAttachments eq true&$orderby=receivedDateTime desc&$top={top}"
        fallback_url = f"{base_url}?$orderby=receivedDateTime desc&$top={top}"
        messages = []
        next_url = messages_url
        attempted_fallback = False
        while next_url:
            resp_raw = requests.get(next_url, headers=headers, timeout=30)
            resp = resp_raw.json()
            if resp_raw.status_code >= 400:
                error_code = (resp.get("error") or {}).get("code")
                if not attempted_fallback and error_code == "InefficientFilter":
                    next_url = fallback_url
                    attempted_fallback = True
                    continue
                break
            messages.extend(resp.get("value", []))
            if not fetch_all and len(messages) >= limit:
                messages = messages[:limit]
                break
            next_url = resp.get("@odata.nextLink") if fetch_all else None

        attachments: List[AttachmentPayload] = []
        if not messages:
            return attachments

        max_workers = min(8, len(messages))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self._fetch_attachments_for_message, message, headers, user): message.get("id")
                for message in messages
            }
            for future in as_completed(future_map):
                try:
                    attachments.extend(future.result() or [])
                except Exception:
                    pass
        return attachments



class ResumeIngestor:
    def __init__(
        self,
        scorer: Optional['GeminiEvaluator'] = None,
        uploader: Optional['GoogleStorageUploader'] = None,
        max_workers: Optional[int] = None,
        score_workers: Optional[int] = None,
    ):
        # REMOVED the invalid import: from .ai import GeminiEvaluator
        
        self.scorer = scorer
        # If GoogleStorageUploader is in this file, use it directly. 
        # If it's in a separate file (e.g. storage.py), keep the import but fix the path.
        # Assuming for now it is in services.py or imported at top:
        self.uploader = uploader or GoogleStorageUploader() 
        
        self.max_workers = max_workers or getattr(settings, "INGEST_MAX_WORKERS", 4)
        self.score_workers = score_workers or getattr(settings, "INGEST_SCORE_WORKERS", self.max_workers)
        self._scorer_local = threading.local()

    def _get_scorer(self) -> 'GeminiEvaluator':
        # REMOVED the invalid import here too.
        if not getattr(self._scorer_local, "instance", None):
            # Just instantiate it directly since it's in the same file
            self._scorer_local.instance = self.scorer or GeminiEvaluator()
        return self._scorer_local.instance

    def _score_resume_for_jobs(self, resume: Resume, text_content: str, jobs: List[JobDescription]) -> None:
        if not jobs:
            return
        
        max_workers = max(1, min(self.score_workers, len(jobs)))

        def score_job(job: JobDescription):
            close_old_connections()
            scorer = self._get_scorer()
            try:
                total, detail = scorer.score_resume_against_job(text_content, job)
                return job, total, detail
            finally:
                close_old_connections()

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(score_job, job): job for job in jobs}
            for future in as_completed(future_map):
                try:
                    job, total, detail = future.result()
                    results.append((job, total, detail))
                except Exception as exc:
                    job_ref = future_map[future]
                    logger.exception("Scoring failed", extra={"job_id": job_ref.id, "error": str(exc)})

        for job, total, detail in results:
            # 1. Create/Update the score object
            score_obj, created = ResumeScore.objects.update_or_create(
                resume=resume,
                job=job,
                defaults={"total_score": total, "detail": detail},
            )
            
            # 2. TRIGGER AUTOMATION (Assuming check_and_process_automation is defined in this file)
            try:
                check_and_process_automation(score_obj)
            except Exception as e:
                logger.error(f"Automation trigger failed for resume {resume.id}: {e}")

    def _record_failure(self, attachment: AttachmentPayload, jobs: List[JobDescription], error: Exception) -> None:
        try:
            sender_clean = clean_string(attachment.sender or "") if attachment.sender else None
            candidate_name = clean_string(attachment.attachment_name.rsplit(".", 1)[0])[:255]
            
            resume, _ = Resume.objects.get_or_create(
                message_id=attachment.message_id,
                attachment_name=attachment.attachment_name,
                defaults={
                    "sender": sender_clean,
                    "candidate_email": sender_clean,
                    "candidate_name": candidate_name,
                    "text_content": "",
                    "received_at": attachment.received_at,
                    "source": attachment.source or ResumeSource.EMAIL,
                },
            )
            
            detail = [{"title": "Processing failed", "score": 0, "notes": truncate(str(error), 200)}]
            for job in jobs:
                ResumeScore.objects.update_or_create(
                    resume=resume,
                    job=job,
                    defaults={"total_score": 0.0, "detail": detail},
                )
        except Exception:
            pass

    def _ingest_single(self, attachment: AttachmentPayload, jobs: List[JobDescription]) -> int:
        close_old_connections()
        try:
            raw_text = extract_text_from_attachment(attachment.attachment_name, attachment.content)
            text_content = clean_string(raw_text)
            phone = extract_phone_number(text_content)
            text_email = extract_email_from_text(text_content)
            
            sender_clean = clean_string(attachment.sender or "") if attachment.sender else None
            candidate_email = text_email or sender_clean

            storage_path = f"resumes/{attachment.message_id}/{attachment.attachment_name}"
            content_type = "application/pdf" if attachment.attachment_name.lower().endswith(".pdf") else "text/plain"
            
            file_url = truncate(
                self.uploader.upload(storage_path, attachment.content, content_type=content_type) or "",
                2000,
            )
            candidate_name = clean_string(attachment.attachment_name.rsplit(".", 1)[0])[:255]

            # CHECK EXISTING
            existing_resume = None
            if candidate_email or phone:
                query = Q()
                if candidate_email:
                    query |= Q(candidate_email=candidate_email) | Q(sender=candidate_email)
                if phone:
                    query |= Q(candidate_phone=phone)
                existing_resume = Resume.objects.filter(query).order_by('-received_at').first()

            # CREATE OR UPDATE
            if existing_resume:
                logger.info(f"Updating existing resume for {candidate_email or phone}")
                resume = existing_resume
                resume.file_url = file_url
                resume.text_content = text_content
                resume.received_at = attachment.received_at
                resume.message_id = attachment.message_id
                resume.attachment_name = attachment.attachment_name
                
                if candidate_email: resume.candidate_email = candidate_email
                if phone: resume.candidate_phone = phone
                if candidate_name: resume.candidate_name = candidate_name

                resume.status = 'PENDING'
                resume.human_score = None
                resume.human_score_breakdown = {}
                resume.is_active_seeker = infer_active_seeker(sender_clean, candidate_email, attachment.source)
                resume.save()
                log_activity(resume, 'INGESTED', f"Updated via {attachment.source or 'Unknown'}")
            else:
                logger.info(f"Creating new resume for {candidate_email or phone}")
                resume = Resume.objects.create(
                    message_id=attachment.message_id,
                    attachment_name=attachment.attachment_name,
                    sender=sender_clean,
                    candidate_email=candidate_email,
                    candidate_phone=phone,
                    candidate_name=candidate_name,
                    text_content=text_content,
                    file_url=file_url or "",
                    received_at=attachment.received_at,
                    source=attachment.source or ResumeSource.EMAIL,
                    is_active_seeker=infer_active_seeker(sender_clean, candidate_email, attachment.source),
                    status='PENDING'
                )
                log_activity(resume, 'INGESTED', f"New application via {attachment.source or 'Unknown'}")

            self._score_resume_for_jobs(resume, text_content, jobs)
            return 1

        except Exception as exc:
            logger.exception("Resume ingest failed", extra={"error": str(exc)})
            self._record_failure(attachment, jobs, exc)
            return 0
        finally:
            close_old_connections()

    def ingest(self, attachments: List[AttachmentPayload], jobs: Optional[List[JobDescription]] = None) -> int:
        if not attachments:
            return 0

        jobs = jobs or list(JobDescription.objects.filter(active=True))
        max_workers = max(1, min(self.max_workers, len(attachments)))
        saved_count = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._ingest_single, attachment, jobs) for attachment in attachments]
            for future in as_completed(futures):
                try:
                    saved_count += future.result() or 0
                except Exception:
                    pass
        return saved_count


def populate_resume_text(resume: Resume, uploader: Optional[GoogleStorageUploader] = None) -> bool:
    if resume.text_content:
        return False
    uploader = uploader or GoogleStorageUploader()
    content_bytes: Optional[bytes] = None
    try:
        if resume.file_url:
            if resume.file_url.startswith("http"):
                resp = requests.get(resume.file_url, timeout=20)
                if resp.status_code < 400:
                    content_bytes = resp.content
            else:
                content_bytes = uploader.download_blob(resume.file_url)
    except Exception as exc:
        record_error("populate_resume_text", str(exc), resume=resume)
        return False

    if not content_bytes:
        return False
    text_content = clean_string(extract_text_from_attachment(resume.attachment_name, content_bytes))
    if not text_content:
        return False
    resume.text_content = text_content
    resume.save(update_fields=["text_content"])
    return True


# jobs/automation.py

def check_and_process_automation(resume_score):
    resume = resume_score.resume
    score_val = resume_score.total_score
    threshold = getattr(settings, 'AUTO_REJECTION_THRESHOLD', 60) 

    # 1. AUTO REJECTION LOGIC
    if score_val < threshold:
        # Only update and log if it's a *new* rejection
        if resume.status != 'AUTO_REJECTED':
            resume.status = 'AUTO_REJECTED'
            resume.save()
            log_activity(resume, 'STATUS_CHANGE', f"Auto-rejected: Score {score_val} < {threshold}")

        # --- EMAIL SENDING DISABLED (COMMENTED OUT) ---
        # if not resume.rejection_email_sent and resume.candidate_email:
        #     subject = f"Update regarding your application for {resume_score.job.name}"
        #     message = (
        #         f"Dear {resume.candidate_name or 'Candidate'},\n\n"
        #         f"Thank you for your interest in the {resume_score.job.name} position. "
        #         "We appreciate the time you took to apply.\n\n"
        #         "After reviewing your application, we have decided not to proceed with your candidacy "
        #         "at this time. We will keep your resume on file for future openings.\n\n"
        #         "Best regards,\nHR Team"
        #     )
        #     try:
        #         send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [resume.candidate_email], fail_silently=False)
        #         
        #         resume.rejection_email_sent = True
        #         resume.save()
        #         
        #         log_activity(resume, 'EMAIL_SENT', "Auto-rejection email sent to candidate.")
        #         
        #     except Exception as e:
        #         record_error("automation:email", str(e))
        # ----------------------------------------------

    # 2. SUCCESS LOGIC (Qualified)
    else:
        # Only move to NEW if it is currently PENDING.
        if resume.status == 'PENDING':
            resume.status = 'NEW'
            resume.save()
            log_activity(resume, 'STATUS_CHANGE', f"Qualified as NEW (Score: {score_val})")