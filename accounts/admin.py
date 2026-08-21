from django.contrib import admin

from .models import LegalAcceptance


@admin.register(LegalAcceptance)
class LegalAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("user", "version", "accepted_at")
    list_filter = ("version", "accepted_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("user", "version", "accepted_at")
