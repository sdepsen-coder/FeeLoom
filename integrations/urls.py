from django.urls import path

from .views import etsy_callback, etsy_connect, etsy_disconnect, etsy_sync


urlpatterns = [
    path("etsy/connect/<int:shop_id>/", etsy_connect, name="etsy_connect"),
    path("etsy/callback/", etsy_callback, name="etsy_callback"),
    path("etsy/sync/<int:shop_id>/", etsy_sync, name="etsy_sync"),
    path("etsy/disconnect/<int:shop_id>/", etsy_disconnect, name="etsy_disconnect"),
]
