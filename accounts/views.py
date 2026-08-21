from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.text import slugify

from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY

from .forms import SignupForm
from .models import BetaInvite, LegalAcceptance


class FeeLoomPasswordResetView(auth_views.PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def dispatch(self, request, *args, **kwargs):
        if not settings.EMAIL_DELIVERY_ENABLED:
            return render(request, "registration/password_reset_unavailable.html")
        return super().dispatch(request, *args, **kwargs)


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
        login(request, user)
        request.session[ACTIVE_SHOP_SESSION_KEY] = shop.id
        return redirect("getting_started")

    return render(request, "accounts/signup.html", {"form": form})
