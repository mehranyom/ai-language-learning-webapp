import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator


def job_dir(instance, filename):
    # jobs/YYYY/MM/DD/<job_uuid>/<filename>
    return f"jobs/{instance.created_at:%Y/%m/%d}/{instance.job_uuid}/{filename}"

class TranscriptionJob(models.Model):
    def __str__(self):
        return f"{self.title} - {self.job_uuid} - {self.status}"
    # Ownership
    owner = models.ForeignKey(get_user_model(), null=True, blank=True, on_delete=models.SET_NULL)

    # Stable ID for file paths
    job_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    # A) Ingestion / Source
    youtube_url = models.URLField()
    youtube_id = models.CharField(max_length=32, blank=True)
    title = models.CharField(max_length=300, blank=True)
    channel_title = models.CharField(max_length=300, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    duration_sec = models.FloatField(null=True, blank=True)

    # B) Storage keys / Artifacts (local now, S3 later)
    source_audio = models.FileField(upload_to=job_dir, blank=True)  # e.g., source.mp3
    wav_audio = models.FileField(upload_to=job_dir, blank=True)     # e.g., audio_16k.wav
    transcript_json = models.FileField(upload_to=job_dir, blank=True)
    transcript_vtt = models.FileField(upload_to=job_dir, blank=True)

    # Processing/meta
    language = models.CharField(max_length=16, blank=True)
    segment_count = models.IntegerField(null=True, blank=True)

    STATUS = [
        ("queued", "Queued"),
        ("downloading", "Downloading"),
        ("converting", "Converting"),
        ("transcribing", "Transcribing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
        ("awaiting_transcription", "Awaiting_transcription"),
    ]
    status = models.CharField(max_length=32, choices=STATUS, default="queued")
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Extend model for proccess bar
    percent = models.PositiveSmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(100)])
    message = models.CharField(max_length=300)

    @property
    def is_ready(self):
        return self.status == "ready"