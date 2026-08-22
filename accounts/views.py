from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.text import slugify
import hashlib

from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY
from dashboard.audit import record_audit

from .forms import ResendVerificationForm, SignupForm
from .models import BetaInvite, LegalAcceptance
from .rate_limits import client_ip, clear_limit, is_limited, rate_limited_response, record_hit
from .tokens import email_verification_token


class FeeLoomLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def rate_values(self):
        username = self.request.POST.get("username", "").strip().lower()
        ip = client_ip(self.request)
        return ip, f"{ip}:{username}"

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            ip, identity = self.rate_values()
            if is_limited("login-ip", ip, settings.LOGIN_IP_FAILURE_LIMIT) or is_limited(
                "login-identity", identity, settings.LOGIN_IDENTITY_FAILURE_LIMIT
            ):
                return rate_limited_response(request, settings.LOGIN_FAILURE_WINDOW)
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        ip, identity = self.rate_values()
        record_hit("login-ip", ip, settings.LOGIN_FAILURE_WINDOW)
        record_hit("login-identity", identity, settings.LOGIN_FAILURE_WINDOW)
        return super().form_invalid(form)

    def form_valid(self, form):
        _, identity = self.rate_values()
        clear_limit("login-identity", identity)
        return super().form_valid(form)


class FeeLoomPasswordResetView(auth_views.PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    html_email_template_name = "registration/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def dispatch(self, request, *args, **kwargs):
        if not settings.EMAIL_DELIVERY_ENABLED:
            return render(request, "registration/password_reset_unavailable.html")
        if request.method == "POST":
            email = request.POST.get("email", "").strip().lower()
            ip = client_ip(request)
            if is_limited("reset-ip", ip, settings.EMAIL_REQUEST_IP_LIMIT) or is_limited(
                "reset-email", email, settings.EMAIL_REQUEST_ADDRESS_LIMIT
            ):
                return rate_limited_response(request, settings.EMAIL_REQUEST_RATE_WINDOW)
            record_hit("reset-ip", ip, settings.EMAIL_REQUEST_RATE_WINDOW)
            record_hit("reset-email", email, settings.EMAIL_REQUEST_RATE_WINDOW)
        return super().dispatch(request, *args, **kwargs)


def send_verification_email(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    verification_url = request.build_absolute_uri(
        reverse("verify_email", args=[uid, token])
    )
    message = render_to_string(
        "accounts/verification_email.txt",
        {"user": user, "verification_url": verification_url},
    )
    html_message = render_to_string(
        "accounts/verification_email.html",
        {"user": user, "verification_url": verification_url},
    )
    send_mail(
        "Verify your FeeLoom email",
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
        html_message=html_message,
    )


def unique_workspace_slug(name):
    base = slugify(name)[:150] or "workspace"
    candidate = base
    suffix = 2
    while Workspace.objects.filter(slug=candidate).exists():
        candidate = f"{base[:145]}-{suffix}"
        suffix += 1
    return candidate


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        ip = client_ip(request)
        if is_limited("signup-ip", ip, settings.SIGNUP_IP_LIMIT):
            return rate_limited_response(request, settings.SIGNUP_RATE_WINDOW)
        record_hit("signup-ip", ip, settings.SIGNUP_RATE_WINDOW)

    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            invite = BetaInvite.objects.select_for_update().filter(
                code__iexact=form.cleaned_data["invite_code"]
            ).first()
            if not invite or not invite.is_available:
                form.add_error("invite_code", "This invite code is no longer available.")
                return render(request, "accounts/signup.html", {"form": form}, status=400)
            user = form.save(commit=False)
            user.email = form.cleaned_data["email"]
            user.is_active = not settings.EMAIL_VERIFICATION_REQUIRED
            user.save()
            workspace = Workspace.objects.create(
                name=form.cleaned_data["workspace_name"],
                slug=unique_workspace_slug(form.cleaned_data["workspace_name"]),
                owner=user,
            )
            Membership.objects.create(
                workspace=workspace,
                user=user,
                role=Membership.Role.OWNER,
            )
            shop = Shop.objects.create(
                workspace=workspace,
                name=form.cleaned_data["shop_name"],
            )
            LegalAcceptance.objects.create(
                user=user,
                version=settings.FEELOOM_LEGAL_VERSION,
            )
            invite.use_count += 1
            invite.save(update_fields=["use_count"])
            record_audit(
                request,
                workspace=workspace,
                shop=shop,
                user=user,
                action="account.created",
                summary="Owner account and workspace created",
            )
            if settings.EMAIL_VERIFICATION_REQUIRED:
                send_verification_email(request, user)
        if settings.EMAIL_VERIFICATION_REQUIRED:
            return render(
                request,
                "accounts/verification_sent.html",
                {"verification_email": user.email},
            )
        login(request, user)
        request.session[ACTIVE_SHOP_SESSION_KEY] = shop.id
        return redirect("getting_started")

    return render(request, "accounts/signup.html", {"form": form})


def verify_email(request, uidb64, token):
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(id=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user and not user.is_active and email_verification_token.check_token(user, token):
        user.is_active = True
        user.save(update_fields=["is_active"])
        login(request, user)
        membership = user.workspace_memberships.select_related("workspace").filter(
            is_active=True
        ).first()
        first_shop = (
            membership.workspace.shops.filter(is_active=True).first()
            if membership
            else None
        )
        if first_shop:
            request.session[ACTIVE_SHOP_SESSION_KEY] = first_shop.id
        return redirect("getting_started")
    return render(request, "accounts/verification_invalid.html", status=400)


def resend_verification(request):
    if not settings.EMAIL_VERIFICATION_REQUIRED:
        return render(request, "accounts/verification_unavailable.html")
    form = ResendVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        ip = client_ip(request)
        if is_limited("verify-ip", ip, settings.EMAIL_REQUEST_IP_LIMIT) or is_limited(
            "verify-address", email, settings.EMAIL_REQUEST_ADDRESS_LIMIT
        ):
            return rate_limited_response(request, settings.EMAIL_REQUEST_RATE_WINDOW)
        record_hit("verify-ip", ip, settings.EMAIL_REQUEST_RATE_WINDOW)
        record_hit("verify-address", email, settings.EMAIL_REQUEST_RATE_WINDOW)
        throttle_key = f"verification-email:{hashlib.sha256(email.encode()).hexdigest()}"
        if cache.add(throttle_key, True, timeout=60):
            user = User.objects.filter(email__iexact=email, is_active=False).first()
            if user:
                send_verification_email(request, user)
        return redirect("verification_resent")
    return render(request, "accounts/resend_verification.html", {"form": form})
