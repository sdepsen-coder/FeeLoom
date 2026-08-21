import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from workspaces.models import Membership, Shop
from workspaces.selectors import current_membership

from .crypto import encrypt_token
from .etsy import EtsyAPIError, exchange_code, get_owner_shop, new_oauth_request
from .models import EtsyConnection
from .sync import sync_connection


OAUTH_SESSION_KEY = "etsy_oauth_request"


def manageable_shop(request, shop_id):
    membership = current_membership(request.user)
    if not membership or membership.role not in {Membership.Role.OWNER, Membership.Role.MANAGER}:
        raise PermissionDenied
    return get_object_or_404(Shop, id=shop_id, workspace=membership.workspace, is_active=True)


def redirect_uri(request):
    return settings.ETSY_REDIRECT_URI or request.build_absolute_uri(reverse("etsy_callback"))


@login_required
def etsy_connect(request, shop_id):
    shop = manageable_shop(request, shop_id)
    if not settings.ETSY_API_KEY or not settings.ETSY_SHARED_SECRET:
        messages.error(request, "Add the Etsy API credentials before connecting a shop.")
        return redirect("shops")
    destination, state, verifier = new_oauth_request(redirect_uri(request))
    request.session[OAUTH_SESSION_KEY] = {
        "state": state,
        "verifier": verifier,
        "shop_id": shop.id,
        "redirect_uri": redirect_uri(request),
    }
    return redirect(destination)


@login_required
def etsy_callback(request):
    oauth_request = request.session.pop(OAUTH_SESSION_KEY, None)
    returned_state = request.GET.get("state", "")
    if not oauth_request or not secrets.compare_digest(oauth_request["state"], returned_state):
        messages.error(request, "The Etsy connection request expired. Please try again.")
        return redirect("shops")
    shop = manageable_shop(request, oauth_request["shop_id"])
    if request.GET.get("error"):
        messages.error(request, request.GET.get("error_description") or "Etsy access was not granted.")
        return redirect("shops")
    code = request.GET.get("code", "")
    if not code:
        messages.error(request, "Etsy did not return an authorization code.")
        return redirect("shops")
    try:
        payload = exchange_code(code, oauth_request["verifier"], oauth_request["redirect_uri"])
        access_token = payload["access_token"]
        etsy_user_id = access_token.split(".", 1)[0]
        etsy_shop = get_owner_shop(etsy_user_id, access_token)
        EtsyConnection.objects.update_or_create(
            shop=shop,
            defaults={
                "etsy_user_id": etsy_user_id,
                "access_token_ciphertext": encrypt_token(access_token),
                "refresh_token_ciphertext": encrypt_token(payload["refresh_token"]),
                "token_expires_at": timezone.now()
                + timedelta(seconds=int(payload.get("expires_in", 3600))),
                "scopes": payload.get("scope", ""),
                "last_error": "",
                "is_active": True,
            },
        )
        shop.external_shop_id = str(etsy_shop["shop_id"])
        shop.currency = etsy_shop.get("currency_code") or shop.currency
        shop.save(update_fields=["external_shop_id", "currency"])
        messages.success(request, f"{shop.name} is connected to Etsy.")
    except (EtsyAPIError, KeyError, ValueError) as exc:
        messages.error(request, f"Etsy connection failed: {exc}")
    return redirect("shops")


@login_required
def etsy_sync(request, shop_id):
    if request.method != "POST":
        return redirect("shops")
    shop = manageable_shop(request, shop_id)
    connection = get_object_or_404(EtsyConnection, shop=shop, is_active=True)
    try:
        run = sync_connection(connection)
        messages.success(
            request,
            f"Etsy sync complete: {run.imported_orders} new, {run.updated_orders} updated.",
        )
    except Exception as exc:
        messages.error(request, f"Etsy sync failed: {exc}")
    return redirect("shops")


@login_required
def etsy_disconnect(request, shop_id):
    if request.method != "POST":
        return redirect("shops")
    shop = manageable_shop(request, shop_id)
    EtsyConnection.objects.filter(shop=shop).delete()
    shop.external_shop_id = ""
    shop.save(update_fields=["external_shop_id"])
    messages.success(request, f"{shop.name} was disconnected from Etsy.")
    return redirect("shops")
