from django.contrib import admin

from .models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("category", "workspace", "shop", "user", "rating", "status", "created_at")
    list_filter = ("status", "category", "rating", "created_at")
    search_fields = ("message", "workspace__name", "shop__name", "user__username")
    readonly_fields = ("workspace", "shop", "user", "category", "rating", "message", "page_path", "created_at")
