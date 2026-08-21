from django.conf import settings


def product_flags(request):
    return {"email_delivery_enabled": settings.EMAIL_DELIVERY_ENABLED}
