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


def error_context(request, *, code, title, message):
    authenticated = request.user.is_authenticated
    return {
        "error_code": code,
        "error_title": title,
        "error_message": message,
        "action_url": "/" if authenticated else "/accounts/login/",
        "action_label": "Back to overview" if authenticated else "Sign in",
    }


def permission_denied(request, exception=None):
    return render(
        request,
        "errors/error.html",
        error_context(
            request,
            code="403",
            title="Access denied",
            message="This account does not have permission to open that page.",
        ),
        status=403,
    )


def page_not_found(request, exception=None):
    return render(
        request,
        "errors/error.html",
        error_context(
            request,
            code="404",
            title="Page not found",
            message="The page may have moved, or the address may be incorrect.",
        ),
        status=404,
    )


def server_error(request):
    return render(
        request,
        "errors/error.html",
        error_context(
            request,
            code="500",
            title="Something went wrong",
            message="FeeLoom could not complete that request. Please try again shortly.",
        ),
        status=500,
    )
