from django.contrib import admin

from .models import AuditEvent, Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("category", "workspace", "shop", "user", "rating", "status", "created_at")
    list_filter = ("status", "category", "rating", "created_at")
    search_fields = ("message", "workspace__name", "shop__name", "user__username")
    readonly_fields = ("workspace", "shop", "user", "category", "rating", "message", "page_path", "created_at")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("action", "workspace", "shop", "user", "request_id", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("action", "summary", "workspace__name", "shop__name", "user__username", "request_id")
    readonly_fields = ("workspace", "shop", "user", "action", "summary", "metadata", "request_id", "created_at")
