from django.contrib import admin

from .models import Membership, Shop, Workspace


admin.site.register(Workspace)
admin.site.register(Membership)
admin.site.register(Shop)
