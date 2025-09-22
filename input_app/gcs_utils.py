from datetime import timedelta
from google.cloud import storage
from django.conf import settings

def gcs_client():
    return storage.Client(
        credentials=settings.GS_CREDENTIALS,
        project=settings.GS_CREDENTIALS.project_id,
    )

def signed_get_url(object_key, minutes=15):
    client = gcs_client()
    blob = client.bucket(settings.GS_BUCKET_NAME).blob(object_key)
    return blob.generate_signed_url(version="v4", expiration=timedelta(minutes=minutes), method="GET")

def vtt_text(object_key):
    client = gcs_client()
    blob = client.bucket(settings.GS_BUCKET_NAME).blob(object_key)
    return blob.download_as_text()

def signed_put_url(object_key, content_type, minutes=15):
    client = gcs_client()
    blob = client.bucket(settings.GS_BUCKET_NAME).blob(object_key)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=minutes),
        method="PUT",
        content_type=content_type,  # MUST match the header the worker sends
    )