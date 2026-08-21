from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from workspaces.models import Shop, Workspace

from .importers import CSVImportError, import_etsy_orders
from .models import FeeLine, ImportBatch, Order, ProductCost


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


class EtsyCSVImportTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="csv-owner")
        workspace = Workspace.objects.create(
            name="CSV Studio",
            slug="csv-studio",
            owner=user,
        )
        self.shop = Shop.objects.create(workspace=workspace, name="CSV Shop")
        ProductCost.objects.create(
            shop=self.shop,
            sku="MUG-1",
            materials=Decimal("4.00"),
            packaging=Decimal("1.00"),
            labor=Decimal("4.00"),
            overhead=Decimal("1.00"),
        )

    def csv_file(self, body, name="EtsySoldOrders2026.csv"):
        return SimpleUploadedFile(name, body.encode("utf-8"), content_type="text/csv")

    def valid_csv(self):
        return self.csv_file(
            "Sale Date,Order ID,Number of Items,Currency,Order Value,"
            "Discount Amount,Shipping,Card Processing Fees,SKU\n"
            "08/15/2026,ETSY-200,1,USD,40.00,5.00,0,1.45,MUG-1\n"
        )

    def test_import_creates_calculated_order_and_fee_breakdown(self):
        batch, errors = import_etsy_orders(self.shop, self.valid_csv())

        order = Order.objects.get(shop=self.shop, external_order_id="ETSY-200")
        self.assertEqual(errors, [])
        self.assertEqual(batch.status, ImportBatch.Status.COMPLETED)
        self.assertEqual(batch.imported_rows, 1)
        self.assertEqual(order.gross_sales, Decimal("35.00"))
        self.assertEqual(order.product_cost, Decimal("10.00"))
        self.assertEqual(order.marketplace_fees, Decimal("3.93"))
        self.assertEqual(order.net_profit, Decimal("21.07"))
        self.assertEqual(order.profit_status, Order.ProfitStatus.PROFITABLE)
        self.assertEqual(FeeLine.objects.filter(order=order).count(), 3)

    def test_duplicate_order_is_skipped(self):
        import_etsy_orders(self.shop, self.valid_csv())

        batch, errors = import_etsy_orders(self.shop, self.valid_csv())

        self.assertEqual(errors, [])
        self.assertEqual(batch.imported_rows, 0)
        self.assertEqual(batch.skipped_rows, 1)
        self.assertEqual(Order.objects.filter(shop=self.shop).count(), 1)

    def test_invalid_headers_are_rejected_without_batch(self):
        invalid = self.csv_file("Date,Receipt,Amount\n2026-08-15,1,20\n")

        with self.assertRaises(CSVImportError):
            import_etsy_orders(self.shop, invalid)

        self.assertFalse(ImportBatch.objects.exists())

    def test_bad_row_is_reported_without_partial_order(self):
        invalid = self.csv_file(
            "Sale Date,Order ID,Order Value\nnot-a-date,ETSY-BAD,20.00\n"
        )

        batch, errors = import_etsy_orders(self.shop, invalid)

        self.assertEqual(batch.status, ImportBatch.Status.FAILED)
        self.assertEqual(batch.imported_rows, 0)
        self.assertIn("Invalid sale date", errors[0])
        self.assertFalse(Order.objects.exists())
