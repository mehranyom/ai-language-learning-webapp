# input_app/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("", views.upload_page, name="upload_page"),         # GET
    path("submit/", views.submit_url, name="submit_url"),    # POST
    path("jobs/<uuid:job_uuid>/", views.job_detail, name="job_detail"),
]