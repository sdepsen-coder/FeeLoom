from django.urls import path

from .views import activity, beta_accounts, beta_invites, dashboard, delete_workspace, download_sample_csv, export_sales, export_workspace_data, feedback, feedback_inbox, getting_started, import_sales, privacy_data, product_costs, sale_detail, sales_table, shops, switch_shop, system_status, toggle_invite, toggle_shop, update_feedback_status


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
    path("system-status/", system_status, name="system_status"),
    path("system-accounts/", beta_accounts, name="beta_accounts"),
    path("system-feedback/", feedback_inbox, name="feedback_inbox"),
    path("system-feedback/<int:feedback_id>/status/", update_feedback_status, name="update_feedback_status"),
    path("getting-started/", getting_started, name="getting_started"),
    path("sample-data/etsy-orders.csv", download_sample_csv, name="download_sample_csv"),
]
