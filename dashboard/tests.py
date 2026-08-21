from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sales.models import ImportBatch, Order, OrderItem, ProductCost
from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY, ALL_SHOPS_VALUE


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="test-pass-123")
        self.workspace = Workspace.objects.create(
            name="Demo Studio",
            slug="demo-studio",
            owner=self.user,
        )
        Membership.objects.create(
            workspace=self.workspace,
            user=self.user,
            role=Membership.Role.OWNER,
        )
        self.shop = Shop.objects.create(workspace=self.workspace, name="Demo Shop")
        self.order = Order.objects.create(
            shop=self.shop,
            external_order_id="ORDER-100",
            ordered_at=timezone.now(),
            item_revenue=Decimal("40.00"),
            marketplace_fees=Decimal("4.00"),
            shipping_cost=Decimal("5.00"),
            product_cost=Decimal("11.00"),
            net_profit=Decimal("20.00"),
            margin_percent=Decimal("50.00"),
            profit_status=Order.ProfitStatus.PROFITABLE,
        )
        OrderItem.objects.create(
            order=self.order,
            sku="MUG-100",
            title="Ceramic Mug",
            quantity=1,
            unit_price=Decimal("40.00"),
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_dashboard_and_sales_show_current_shop_data(self):
        self.client.force_login(self.user)

        dashboard_response = self.client.get(reverse("dashboard"))
        sales_response = self.client.get(reverse("sales_table"))

        self.assertContains(dashboard_response, "USD 40.00")
        self.assertContains(sales_response, "ORDER-100")
        self.assertContains(sales_response, "Ceramic Mug")

    def test_other_workspace_order_is_hidden(self):
        other_user = User.objects.create_user(username="other")
        other_workspace = Workspace.objects.create(
            name="Other Studio",
            slug="other-studio",
            owner=other_user,
        )
        other_shop = Shop.objects.create(workspace=other_workspace, name="Other Shop")
        hidden = Order.objects.create(
            shop=other_shop,
            external_order_id="HIDDEN-1",
            ordered_at=timezone.now(),
        )
        self.client.force_login(self.user)

        list_response = self.client.get(reverse("sales_table"))
        detail_response = self.client.get(reverse("sale_detail", args=[hidden.id]))

        self.assertNotContains(list_response, "HIDDEN-1")
        self.assertEqual(detail_response.status_code, 404)

    def test_product_cost_is_saved_to_current_shop(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("product_costs"),
            {
                "sku": "mug-100",
                "title": "Ceramic Mug",
                "materials": "3.00",
                "packaging": "1.00",
                "labor": "4.00",
                "overhead": "2.00",
            },
        )

        cost = ProductCost.objects.get(shop=self.shop, sku="MUG-100")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cost.unit_cost, Decimal("10.00"))

    def test_csv_export_contains_filtered_shop_orders(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("export_sales"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("ORDER-100", response.content.decode())

    def test_csv_export_escapes_spreadsheet_formulas(self):
        self.order.external_order_id = "=DANGEROUS()"
        self.order.save(update_fields=["external_order_id"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("export_sales"))

        self.assertIn("'=DANGEROUS()", response.content.decode())

    def test_orders_csv_can_be_imported_from_dashboard(self):
        self.client.force_login(self.user)
        uploaded = SimpleUploadedFile(
            "EtsySoldOrders2026.csv",
            (
                "Sale Date,Order ID,Number of Items,Currency,Order Value,"
                "Discount Amount,Shipping,Card Processing Fees,SKU\n"
                "08/16/2026,ETSY-300,1,USD,30.00,0,0,1.15,NEW-1\n"
            ).encode(),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("import_sales"), {"shop": self.shop.id, "csv_file": uploaded}
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Order.objects.filter(shop=self.shop, external_order_id="ETSY-300").exists()
        )

    def test_import_result_cannot_show_other_shop_batch(self):
        other_user = User.objects.create_user(username="batch-owner")
        other_workspace = Workspace.objects.create(
            name="Batch Studio",
            slug="batch-studio",
            owner=other_user,
        )
        other_shop = Shop.objects.create(workspace=other_workspace, name="Batch Shop")
        other_batch = ImportBatch.objects.create(shop=other_shop, file_name="private.csv")
        self.client.force_login(self.user)

        response = self.client.get(reverse("import_sales"), {"batch": other_batch.id})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "private.csv")

    def test_all_shops_combines_orders_and_identifies_shop(self):
        second_shop = Shop.objects.create(workspace=self.workspace, name="Second Shop")
        Order.objects.create(
            shop=second_shop,
            external_order_id="ORDER-200",
            ordered_at=timezone.now(),
            item_revenue=Decimal("60.00"),
            net_profit=Decimal("30.00"),
            profit_status=Order.ProfitStatus.PROFITABLE,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session[ACTIVE_SHOP_SESSION_KEY] = ALL_SHOPS_VALUE
        session.save()

        dashboard_response = self.client.get(reverse("dashboard"))
        sales_response = self.client.get(reverse("sales_table"))

        self.assertContains(dashboard_response, "USD 100.00")
        self.assertContains(sales_response, "ORDER-100")
        self.assertContains(sales_response, "ORDER-200")
        self.assertContains(sales_response, "Second Shop")

    def test_switching_shop_scopes_sales(self):
        second_shop = Shop.objects.create(workspace=self.workspace, name="Second Shop")
        Order.objects.create(
            shop=second_shop,
            external_order_id="ORDER-200",
            ordered_at=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("switch_shop"), {"shop": second_shop.id, "next": reverse("sales_table")}
        )
        sales_response = self.client.get(reverse("sales_table"))

        self.assertRedirects(response, reverse("sales_table"))
        self.assertEqual(self.client.session[ACTIVE_SHOP_SESSION_KEY], second_shop.id)
        self.assertContains(sales_response, "ORDER-200")
        self.assertNotContains(sales_response, "ORDER-100")

    def test_cannot_switch_to_another_workspace_shop(self):
        other_user = User.objects.create_user(username="outsider")
        other_workspace = Workspace.objects.create(
            name="Outside", slug="outside", owner=other_user
        )
        outside_shop = Shop.objects.create(workspace=other_workspace, name="Private Shop")
        self.client.force_login(self.user)

        response = self.client.post(reverse("switch_shop"), {"shop": outside_shop.id})

        self.assertEqual(response.status_code, 404)

    def test_owner_can_add_a_second_shop(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("shops"), {"name": "New Store", "currency": "usd"}
        )

        new_shop = Shop.objects.get(workspace=self.workspace, name="New Store")
        self.assertRedirects(response, reverse("shops"))
        self.assertEqual(new_shop.currency, "USD")
        self.assertEqual(self.client.session[ACTIVE_SHOP_SESSION_KEY], new_shop.id)

    def test_import_rejects_shop_from_another_workspace(self):
        other_user = User.objects.create_user(username="import-outsider")
        other_workspace = Workspace.objects.create(
            name="Import Outside", slug="import-outside", owner=other_user
        )
        outside_shop = Shop.objects.create(workspace=other_workspace, name="Outside Import")
        self.client.force_login(self.user)
        uploaded = SimpleUploadedFile(
            "orders.csv",
            b"Sale Date,Order ID,Order Value\n08/16/2026,PRIVATE-1,30.00\n",
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("import_sales"), {"shop": outside_shop.id, "csv_file": uploaded}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(external_order_id="PRIVATE-1").exists())
