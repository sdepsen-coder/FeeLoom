from django.contrib import admin

from .models import FeeLine, ImportBatch, Order, OrderItem, ProductCost


admin.site.register(ImportBatch)
admin.site.register(Order)
admin.site.register(OrderItem)
admin.site.register(FeeLine)
admin.site.register(ProductCost)
