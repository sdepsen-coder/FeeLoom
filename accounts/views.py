from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.text import slugify

from workspaces.models import Membership, Shop, Workspace

from .forms import SignupForm


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
            Shop.objects.create(
                workspace=workspace,
                name=form.cleaned_data["shop_name"],
            )
        login(request, user)
        return redirect("dashboard")

    return render(request, "accounts/signup.html", {"form": form})
