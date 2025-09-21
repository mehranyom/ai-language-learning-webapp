from django.shortcuts import render
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from .models import TranscriptionJob
from django.http import HttpResponse
from input_app.tasks import prepare_audio
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
import json
from .gcs_utils import signed_get_url


# input_app/views.py

def home(request):
    return HttpResponse("It works! ✅")
# Create your views here.

@require_http_methods(["GET"])
def upload_page(request):
    return render(request, "input_app/upload.html")

@require_http_methods(["POST"])
def submit_url(request):
    url = request.POST.get("youtube_url")
    job = TranscriptionJob.objects.create(
        youtube_url=url,
        owner=request.user if request.user.is_authenticated else None,
        status="queued",
    )
    # try:
    #     media_dir = settings.MEDIA_ROOT  # dev: useu MEDIA_ROOT as scratch
    #     prepare_job_files(job, media_dir)
    # except Exception as e:
    #     job.status = "failed"
    #     job.error_message = str(e)
    #     job.save()
    prepare_audio.delay(job.id)
    return redirect("job_detail", job_uuid=str(job.job_uuid))

def job_detail(request, job_uuid):
    job = get_object_or_404(TranscriptionJob, job_uuid=job_uuid)
    return render(request, "input_app/job_detail.html", {"job": job})

# input_app/views.py
def job_status(request, job_uuid):
    job = get_object_or_404(TranscriptionJob, job_uuid=job_uuid)
    return JsonResponse({
        "status": job.status or "",
        "step": job.step or "",
        "percent": job.percent or 0,
        "message": job.message or "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "title": job.title or "",
        "youtube_id": job.youtube_id or "",
        "duration_sec": float(job.duration_sec or 0),
    })

@csrf_exempt
def worker_heartbeat(request):
    if request.method != "POST": return HttpResponseBadRequest("POST only")
    data = json.loads(request.body or "{}")
    token = request.headers.get("Authorization", "")
    if token != "Bearer super-secret-token":  # match your settings
        return HttpResponseBadRequest("bad token")
    job_uuid = data.get("job_uuid")
    percent  = data.get("percent", 70)
    message  = data.get("message", "Transcribing…")
    try:
        job = TranscriptionJob.objects.get(job_uuid=job_uuid)
    except TranscriptionJob.DoesNotExist:
        return HttpResponseBadRequest("unknown job")
    # keep transcribing percent below 100; let /complete set final ready
    job.status = "transcribing"
    job.step   = "transcribing"
    job.percent = max(job.percent or 0, min(99, int(percent)))
    job.message = message[:255]
    job.save(update_fields=["status","step","percent","message","updated_at"])
    return JsonResponse({"ok": True})


def job_ready(request, job_uuid):
    job = get_object_or_404(TranscriptionJob, job_uuid=job_uuid)
    json_url = signed_get_url(job.transcript_json.name) if job.transcript_json else None
    vtt_url  = signed_get_url(job.transcript_vtt.name)  if job.transcript_vtt  else None
    mp3_url  = signed_get_url(job.source_audio.name)    if job.source_audio    else None
    wav_url  = signed_get_url(job.wav_audio.name)       if job.wav_audio       else None

    return render(request, "input_app/job_ready.html", {
        "job": job,
        "json_url": json_url,
        "vtt_url": vtt_url,
        "mp3_url": mp3_url,
        "wav_url": wav_url,
    })
    # return render(request, "input_app/job_ready.html", {"job": job})