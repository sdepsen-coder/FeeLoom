from django.contrib import admin

from .models import BetaInvite, LegalAcceptance


@admin.register(LegalAcceptance)
class LegalAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("user", "version", "accepted_at")
    list_filter = ("version", "accepted_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("user", "version", "accepted_at")


@admin.register(BetaInvite)
class BetaInviteAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "workspace", "use_count", "max_uses", "expires_at", "is_active")
    list_filter = ("is_active", "expires_at", "created_at")
    search_fields = ("code", "label", "workspace__name", "created_by__username")
    readonly_fields = ("code", "use_count", "created_at")
