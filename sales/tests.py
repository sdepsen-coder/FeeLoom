from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from workspaces.models import Shop, Workspace

from .models import Order, ProductCost


class ProfitCalculationTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="owner")
        workspace = Workspace.objects.create(name="Studio", slug="studio", owner=user)
        self.shop = Shop.objects.create(workspace=workspace, name="Studio Shop")

    def order(self, **overrides):
        values = {
            "shop": self.shop,
            "external_order_id": "ORDER-1",
            "ordered_at": timezone.now(),
            "item_revenue": Decimal("50.00"),
            "shipping_revenue": Decimal("5.00"),
            "marketplace_fees": Decimal("6.00"),
            "ad_fees": Decimal("0.00"),
            "shipping_cost": Decimal("5.00"),
            "product_cost": Decimal("15.00"),
        }
        values.update(overrides)
        return Order(**values)

    def test_calculates_profitable_order(self):
        order = self.order()

        order.calculate_profit()

        self.assertEqual(order.net_profit, Decimal("29.00"))
        self.assertEqual(order.margin_percent.quantize(Decimal("0.01")), Decimal("52.73"))
        self.assertEqual(order.profit_status, Order.ProfitStatus.PROFITABLE)

    def test_marks_loss_and_incomplete_costs(self):
        loss = self.order(product_cost=Decimal("60.00"))
        incomplete = self.order()

        loss.calculate_profit()
        incomplete.calculate_profit(costs_complete=False)

        self.assertEqual(loss.profit_status, Order.ProfitStatus.LOSS)
        self.assertEqual(incomplete.profit_status, Order.ProfitStatus.INCOMPLETE)

    def test_product_unit_cost_combines_all_cost_parts(self):
        cost = ProductCost(
            shop=self.shop,
            sku="MUG-1",
            materials=Decimal("3.00"),
            packaging=Decimal("1.00"),
            labor=Decimal("4.00"),
            overhead=Decimal("2.00"),
        )

        self.assertEqual(cost.unit_cost, Decimal("10.00"))
