from django.shortcuts import render
import os
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from .models import TranscriptionJob
from .services import prepare_job_files
from django.http import HttpResponse

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
    try:
        media_dir = settings.MEDIA_ROOT  # dev: useu MEDIA_ROOT as scratch
        prepare_job_files(job, media_dir)
    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        job.save()
    return redirect("job_detail", job_uuid=str(job.job_uuid))

def job_detail(request, job_uuid):
    job = get_object_or_404(TranscriptionJob, job_uuid=job_uuid)
    return render(request, "input_app/job_detail.html", {"job": job})
