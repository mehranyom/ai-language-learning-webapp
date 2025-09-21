# input_app/urls.py
from django.urls import path
from . import views
from . import worker_api
urlpatterns = [
    path("", views.upload_page, name="upload_page"),         # GET
    path("submit/", views.submit_url, name="submit_url"),    # POST
    path("jobs/<uuid:job_uuid>/", views.job_detail, name="job_detail"),
    path("jobs/<uuid:job_uuid>/status", views.job_status, name="job_status"),
    path("api/worker/ping", worker_api.ping, name="worker_ping"),
    path("api/worker/next", worker_api.next_job, name="worker_next"),
    path("api/worker/complete", worker_api.complete, name="worker_complete"),
    path("api/worker/heartbeat", views.worker_heartbeat, name="worker_heartbeat"),
    path("jobs/<uuid:job_uuid>/view/", views.job_ready, name="job_ready")
]