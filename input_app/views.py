from django.shortcuts import render

# input_app/views.py
from django.http import HttpResponse

def home(request):
    return HttpResponse("It works! ✅")
# Create your views here.
