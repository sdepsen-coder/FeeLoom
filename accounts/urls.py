from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import TemplateView

from .views import FeeLoomLoginView, FeeLoomPasswordResetView, resend_verification, signup, verify_email


urlpatterns = [
    path("login/", FeeLoomLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", signup, name="signup"),
    path("verify-email/<uidb64>/<token>/", verify_email, name="verify_email"),
    path("resend-verification/", resend_verification, name="resend_verification"),
    path(
        "resend-verification/done/",
        TemplateView.as_view(template_name="accounts/verification_resent.html"),
        name="verification_resent",
    ),
    path("password-reset/", FeeLoomPasswordResetView.as_view(), name="password_reset"),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url="/accounts/reset/complete/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
