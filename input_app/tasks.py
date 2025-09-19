import os
import tempfile
import subprocess
from datetime import datetime
from django.core.files import File
from django.db import transaction
from django.utils.timezone import now
from celery import shared_task

from .models import TranscriptionJob

# ---- helpers -------------------------------------------------------

def _update(job: TranscriptionJob, *, step=None, status=None, percent=None, message=None):
    """Small utility to update a few fields without racey read-modify-writes."""
    fields = []
    if step is not None:
        job.step = step; fields.append("step")
    if status is not None:
        job.status = status; fields.append("status")
    if percent is not None:
        job.percent = int(max(0, min(100, percent))); fields.append("percent")
    if message is not None:
        job.message = message[:255]; fields.append("message")
    job.updated_at = now(); fields.append("updated_at")
    job.save(update_fields=fields)

def _progress_hook(job_id):
    """
    Build a yt-dlp progress hook bound to a job id.
    """
    def hook(d):
        # We attach lazily to avoid serializing the model into Celery
        job = TranscriptionJob.objects.get(pk=job_id)
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = int(done * 100 / total) if total else 1
            _update(job, step="downloading", percent=min(40, max(1, pct // 3)),  # map 0–100 download => 0–40 overall
                    message=f"Downloading… {pct}%")
        elif d.get("status") == "finished":
            _update(job, step="downloading", percent=40, message="Download finished.")
    return hook

# ---- the task ------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def prepare_audio(self, job_id: int):
    """
    Download source audio with yt-dlp, convert to 16k mono WAV with ffmpeg,
    store both into GCS via FileFields, and move job to awaiting_transcription.
    """
    from yt_dlp import YoutubeDL

    job = TranscriptionJob.objects.get(pk=job_id)

    # If someone re-queued it, reset progress
    _update(job, step="queued", percent=0, message="Queued")

    with tempfile.TemporaryDirectory() as td:
        mp3_path = os.path.join(td, "source.mp3")
        wav_path = os.path.join(td, "audio_16k.wav")

        # 1) Download
        hook = _progress_hook(job_id)
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(td, "source.%(ext)s"),
            "quiet": True,
            "noprogress": True,
            "progress_hooks": [hook],
            "postprocessors": [
                # ensure MP3 output; alternatively extract-audio straight to wav later
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                _update(job, step="downloading", message="Starting download…", percent=1)
                ydl.download([job.youtube_url])
        except Exception as e:
            _update(job, status="failed", step="queued", message=f"Download error: {e}")
            raise

        # yt-dlp's FFmpegExtractAudio will produce source.mp3 (or .m4a/.mp3 depending); normalize name:
        # If not present, fall back to first audio file in temp dir
        if not os.path.exists(mp3_path):
            # find first .mp3
            for fname in os.listdir(td):
                if fname.endswith(".mp3"):
                    mp3_path = os.path.join(td, fname)
                    break
        if not os.path.exists(mp3_path):
            _update(job, status="failed", message="MP3 not found after download.")
            return

        # 2) Convert to 16k mono PCM WAV
        _update(job, step="converting", message="Converting to 16k WAV…", percent=50)
        cmd = [
            "ffmpeg", "-y",
            "-i", mp3_path,
            "-ac", "1",
            "-ar", "16000",
            "-acodec", "pcm_s16le",
            wav_path,
        ]
        try:
            # Capture minimal output; if ffmpeg is missing, this will error
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        except Exception as e:
            _update(job, status="failed", message=f"ffmpeg error: {e}")
            return

        # 3) Save artifacts to your default storage (GCS via django-storages)
        #    These .save() calls will upload to your bucket using your configured storage
        _update(job, step="converting", message="Uploading artifacts…", percent=58)
        with open(mp3_path, "rb") as fmp3:
            job.source_audio.save("source.mp3", File(fmp3), save=False)
        with open(wav_path, "rb") as fwav:
            job.wav_audio.save("audio_16k.wav", File(fwav), save=False)

        # 4) Finalize
        job.duration_sec = job.duration_sec or 0.0  # keep whatever you already set (optional)
        job.save(update_fields=["source_audio", "wav_audio", "duration_sec"])

        _update(job, status="awaiting_transcription", step="awaiting_transcription",
                percent=65, message="Waiting for GPU worker…")