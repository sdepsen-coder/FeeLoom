from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.conf import settings


def health_check(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok"})


def legal_context():
    return {
        "legal_version": settings.FEELOOM_LEGAL_VERSION,
        "support_email": settings.FEELOOM_SUPPORT_EMAIL,
    }


def privacy_policy(request):
    return render(request, "legal/privacy.html", legal_context())


def terms_of_service(request):
    return render(request, "legal/terms.html", legal_context())
