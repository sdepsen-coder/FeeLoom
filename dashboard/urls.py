from django.urls import path

from .views import activity, beta_invites, dashboard, delete_workspace, download_sample_csv, export_sales, export_workspace_data, feedback, getting_started, import_sales, privacy_data, product_costs, sale_detail, sales_table, shops, switch_shop, toggle_invite, toggle_shop


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
    path("privacy-data/", privacy_data, name="privacy_data"),
    path("privacy-data/export/", export_workspace_data, name="export_workspace_data"),
    path("privacy-data/delete/", delete_workspace, name="delete_workspace"),
    path("beta-invites/", beta_invites, name="beta_invites"),
    path("beta-invites/<int:invite_id>/toggle/", toggle_invite, name="toggle_invite"),
    path("activity/", activity, name="activity"),
    path("getting-started/", getting_started, name="getting_started"),
    path("sample-data/etsy-orders.csv", download_sample_csv, name="download_sample_csv"),
]
