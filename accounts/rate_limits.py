import hashlib

from django.core.cache import cache
from django.shortcuts import render


def client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded_for.split(",", 1)[0].strip() or request.META.get(
        "REMOTE_ADDR", "unknown"
    )


def cache_key(namespace, value):
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"feeloom-rate:{namespace}:{digest}"


def is_limited(namespace, value, limit):
    return int(cache.get(cache_key(namespace, value), 0)) >= limit


def record_hit(namespace, value, window):
    key = cache_key(namespace, value)
    if cache.add(key, 1, timeout=window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
        return 1


def clear_limit(namespace, value):
    cache.delete(cache_key(namespace, value))


def rate_limited_response(request, retry_after):
    response = render(
        request,
        "accounts/rate_limited.html",
        {"retry_after_minutes": max(1, retry_after // 60)},
        status=429,
    )
    response["Retry-After"] = str(retry_after)
    return response
