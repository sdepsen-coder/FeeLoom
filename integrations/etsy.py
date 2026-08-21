import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from .crypto import decrypt_token, encrypt_token


AUTHORIZE_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
API_ROOT = "https://api.etsy.com/v3/application"
OAUTH_SCOPES = "shops_r transactions_r"


class EtsyAPIError(RuntimeError):
    pass


def api_key_header():
    return f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"


def request_json(url, *, method="GET", data=None, access_token="", retries=3):
    encoded_data = urlencode(data).encode() if data is not None else None
    headers = {"Accept": "application/json", "x-api-key": api_key_header()}
    if encoded_data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    for attempt in range(retries + 1):
        request = Request(url, data=encoded_data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                delay = int(retry_after) if retry_after.isdigit() else 2**attempt
                time.sleep(min(delay, 10))
                continue
            try:
                detail = json.loads(exc.read().decode()).get("error", "")
            except (ValueError, UnicodeDecodeError):
                detail = ""
            raise EtsyAPIError(detail or f"Etsy returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(min(2**attempt, 10))
                continue
            raise EtsyAPIError("Etsy could not be reached. Try again shortly.") from exc


def new_oauth_request(redirect_uri):
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.ETSY_API_KEY,
            "redirect_uri": redirect_uri,
            "scope": OAUTH_SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}", state, verifier


def exchange_code(code, verifier, redirect_uri):
    return request_json(
        TOKEN_URL,
        method="POST",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.ETSY_API_KEY,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": verifier,
        },
    )


def refreshed_tokens(refresh_token):
    return request_json(
        TOKEN_URL,
        method="POST",
        data={
            "grant_type": "refresh_token",
            "client_id": settings.ETSY_API_KEY,
            "refresh_token": refresh_token,
        },
    )


def save_tokens(connection, payload):
    connection.access_token_ciphertext = encrypt_token(payload["access_token"])
    connection.refresh_token_ciphertext = encrypt_token(payload["refresh_token"])
    connection.token_expires_at = timezone.now() + timedelta(seconds=int(payload.get("expires_in", 3600)))
    connection.scopes = payload.get("scope", connection.scopes)


def active_access_token(connection):
    if connection.token_expires_at > timezone.now() + timedelta(minutes=5):
        return decrypt_token(connection.access_token_ciphertext)
    payload = refreshed_tokens(decrypt_token(connection.refresh_token_ciphertext))
    save_tokens(connection, payload)
    connection.save(
        update_fields=["access_token_ciphertext", "refresh_token_ciphertext", "token_expires_at", "scopes", "updated_at"]
    )
    return payload["access_token"]


def get_owner_shop(user_id, access_token):
    return request_json(f"{API_ROOT}/users/{user_id}/shops", access_token=access_token)


def get_receipts(shop_id, access_token, *, min_last_modified=None, page_size=100, max_pages=50):
    receipts = []
    page_size = min(max(page_size, 1), 100)
    for page in range(max_pages):
        params = {
            "limit": page_size,
            "offset": page * page_size,
            "sort_on": "updated",
            "sort_order": "desc",
        }
        if min_last_modified:
            params["min_last_modified"] = int(min_last_modified.timestamp())
        payload = request_json(
            f"{API_ROOT}/shops/{shop_id}/receipts?{urlencode(params)}",
            access_token=access_token,
        )
        page_results = payload.get("results", [])
        receipts.extend(page_results)
        total = int(payload.get("count", len(receipts)))
        if not page_results or len(receipts) >= total or len(page_results) < page_size:
            return receipts
    raise EtsyAPIError("The Etsy order history is too large for one sync. Run sync again.")


def get_receipt_payments(shop_id, receipt_id, access_token):
    return request_json(
        f"{API_ROOT}/shops/{shop_id}/receipts/{receipt_id}/payments",
        access_token=access_token,
    ).get("results", [])
