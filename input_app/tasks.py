from celery import shared_task
from django.utils.timezone import now
from .models import TranscriptionJob
from .services import prepare_job_files

from .models import TranscriptionJob

# ---- helpers -------------------------------------------------------

def _update(job: TranscriptionJob, *, step=None, status=None, percent=None, message=None):
    """Small utility to update a few fields without racey read-modify-writes."""
    fields = []
    if step is not None:
        job.step = step
        fields.append("step")
    if status is not None:
        job.status = status
        fields.append("status")
    if percent is not None:
        job.percent = int(max(0, min(100, percent)))
        fields.append("percent")
    if message is not None:
        job.message = message[:255]
        fields.append("message")
    job.updated_at = now()
    fields.append("updated_at")
    job.save(update_fields=fields)

def make_emit_for(job_id: int):
    def emit(step: str, percent: int, message: str):
        job = TranscriptionJob.objects.get(pk=job_id)
        _update(job, step=step, percent=percent, message=message)
    return emit

# ---- the task ------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def prepare_audio(self, job_id: int):
    job = TranscriptionJob.objects.get(pk=job_id)
    _update(job, step="queued", percent=0, message="Queued")

    emit = make_emit_for(job_id)

    try:
        # Reuse your service; it updates FileFields and calls emit() at key points
        prepare_job_files(job, on_progress=emit)
    except Exception as e:
        _update(job, status="failed", message=f"{type(e).__name__}: {e}")
        raise

    # Final transition for the queue/worker flow:
    _update(job, status="awaiting_transcription", step="awaiting_transcription",
            percent=65, message="Waiting for GPU worker…")