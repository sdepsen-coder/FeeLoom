from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sales.models import Order, OrderItem, ProductCost
from workspaces.models import Membership, Shop, Workspace


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
