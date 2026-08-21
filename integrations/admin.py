from django.contrib import admin

from .models import EtsyConnection, EtsySyncRun


@admin.register(EtsyConnection)
class EtsyConnectionAdmin(admin.ModelAdmin):
    list_display = ("shop", "etsy_user_id", "is_active", "last_synced_at", "updated_at")
    readonly_fields = ("access_token_ciphertext", "refresh_token_ciphertext")


@admin.register(EtsySyncRun)
class EtsySyncRunAdmin(admin.ModelAdmin):
    list_display = ("connection", "status", "imported_orders", "updated_orders", "started_at")
