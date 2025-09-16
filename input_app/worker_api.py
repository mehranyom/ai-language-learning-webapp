from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import json
from django.db import transaction
from django.utils.timezone import now
from .models import TranscriptionJob
from .gcs_utils import signed_get_url, signed_put_url

#decorator
def require_worker_auth(view_func):
    def wrapper(request, *args, **kwargs):
        auth_header = request.headers.get("Authorization")
        expected = f"Bearer {settings.WORKER_API_TOKEN}"
        if auth_header != expected:
            return JsonResponse({"error": "Unauthorized"}, status=401)
        return view_func(request, *args, **kwargs)
    return wrapper

@csrf_exempt
@require_worker_auth
def ping(request):
    return JsonResponse({"ok": True, "message": "Worker API is alive!"})


@csrf_exempt
@require_worker_auth
def next_job(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    # If you want, parse worker preferences here (model, language)
    # prefs = json.loads(request.body or b"{}")

    with transaction.atomic():
        job = (
            TranscriptionJob.objects
            .select_for_update()
            .filter(status="awaiting_transcription")
            .order_by("created_at")
            .first()
        )
        if not job:
            return JsonResponse({}, status=204)  # no content

        # Mark as claimed
        job.status = "transcribing"
        job.updated_at = now()
        job.save(update_fields=["status", "updated_at"])

    # Build object keys (we already stored FileFields; use their .name as the key)
    # If you saved to GCS via DEFAULT_FILE_STORAGE, FileField.name is the GCS object key.
    wav_key = job.wav_audio.name
    # Target outputs under the same folder:
    base_prefix = wav_key.rsplit("/", 1)[0]
    json_key = f"{base_prefix}/transcript.json"
    vtt_key  = f"{base_prefix}/transcript.vtt"

    # Signed URLs
    audio_wav_get_url = signed_get_url(wav_key, minutes=20)
    transcript_json_put_url = signed_put_url(json_key, content_type="application/json", minutes=20)
    transcript_vtt_put_url  = signed_put_url(vtt_key,  content_type="text/vtt",        minutes=20)

    # Return contract
    return JsonResponse({
        "job_uuid": str(job.job_uuid),
        "youtube_url": job.youtube_url,
        "youtube_id": job.youtube_id,
        "title": job.title,
        "duration_sec": job.duration_sec,
        "audio_wav_get_url": audio_wav_get_url,
        "transcript_json_put_url": transcript_json_put_url,
        "transcript_vtt_put_url": transcript_vtt_put_url,
        "settings": {
            "model": "faster-whisper-small",
            "vad": True
        },
        # hint for the worker (optional)
        "expires_in_minutes": 20
    }, status=200)


@csrf_exempt
@require_worker_auth
def complete(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body or b"{}")
        job_uuid = data["job_uuid"]
        language = data.get("language")
        segment_count = data.get("segment_count")
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from .models import TranscriptionJob
    job = TranscriptionJob.objects.filter(job_uuid=job_uuid).first()
    if not job:
        return JsonResponse({"error": "Job not found"}, status=404)

    # Derive transcript keys from the wav’s folder (same pattern used in /next)
    if not job.wav_audio or not job.wav_audio.name:
        return JsonResponse({"error": "WAV missing for job"}, status=400)

    base_prefix = job.wav_audio.name.rsplit("/", 1)[0]
    json_key = f"{base_prefix}/transcript.json"
    vtt_key  = f"{base_prefix}/transcript.vtt"

    # Attach to FileFields by setting .name to the object key (GCS backend)
    job.transcript_json.name = json_key
    job.transcript_vtt.name  = vtt_key
    if language: job.language = language
    if segment_count is not None: job.segment_count = segment_count
    job.status = "ready"
    job.updated_at = now()
    job.save(update_fields=[
        "transcript_json","transcript_vtt","language","segment_count","status","updated_at"
    ])

    return JsonResponse({"ok": True})