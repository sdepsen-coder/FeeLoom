from django.urls import path

from .views import dashboard, export_sales, feedback, import_sales, product_costs, sale_detail, sales_table, shops, switch_shop, toggle_shop


urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("sales/", sales_table, name="sales_table"),
    path("sales/export/", export_sales, name="export_sales"),
    path("sales/import/", import_sales, name="import_sales"),
    path("sales/<int:order_id>/", sale_detail, name="sale_detail"),
    path("costs/", product_costs, name="product_costs"),
    path("shops/", shops, name="shops"),
    path("shops/switch/", switch_shop, name="switch_shop"),
    path("shops/<int:shop_id>/toggle/", toggle_shop, name="toggle_shop"),
    path("feedback/", feedback, name="feedback"),
]
