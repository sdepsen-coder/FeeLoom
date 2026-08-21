from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from sales.models import FeeLine, Order, OrderItem, ProductCost
from workspaces.models import Membership, Shop, Workspace


class Command(BaseCommand):
    help = "Create a local FeeLoom demo account and sample sales."

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            username="demo",
            defaults={"email": "demo@feeloom.local"},
        )
        if created:
            user.set_password("demo12345")
            user.save(update_fields=["password"])
        workspace, _ = Workspace.objects.get_or_create(
            slug="demo-studio",
            defaults={"name": "Demo Studio", "owner": user},
        )
        Membership.objects.get_or_create(
            workspace=workspace,
            user=user,
            defaults={"role": Membership.Role.OWNER},
        )
        shop, _ = Shop.objects.get_or_create(
            workspace=workspace,
            name="Thread & Timber",
        )
        ProductCost.objects.update_or_create(
            shop=shop,
            sku="MUG-001",
            defaults={
                "title": "Personalized Ceramic Mug",
                "materials": Decimal("4.20"),
                "packaging": Decimal("1.10"),
                "labor": Decimal("5.50"),
                "overhead": Decimal("1.20"),
            },
        )
        samples = [
            ("ETSY-1048", 46, 5, 0, 7, 12, "Personalized Ceramic Mug", "MUG-001"),
            ("ETSY-1047", 32, 4, 5, 6, 10, "Oak Desk Organizer", "DESK-004"),
            ("ETSY-1046", 24, 3, 0, 5, 15, "Botanical Art Print", "PRINT-012"),
            ("ETSY-1045", 18, 3, 3, 5, 9, "Custom Name Tag", "TAG-008"),
        ]
        for index, (order_id, revenue, fees, ads, shipping, cost, title, sku) in enumerate(samples):
            order, _ = Order.objects.update_or_create(
                shop=shop,
                external_order_id=order_id,
                defaults={
                    "ordered_at": timezone.now() - timedelta(days=index * 2),
                    "item_revenue": Decimal(revenue),
                    "marketplace_fees": Decimal(fees),
                    "ad_fees": Decimal(ads),
                    "shipping_cost": Decimal(shipping),
                    "product_cost": Decimal(cost),
                },
            )
            order.calculate_profit()
            order.save()
            OrderItem.objects.update_or_create(
                order=order,
                sku=sku,
                defaults={"title": title, "quantity": 1, "unit_price": revenue},
            )
            FeeLine.objects.filter(order=order).delete()
            FeeLine.objects.create(
                order=order,
                fee_type=FeeLine.FeeType.TRANSACTION,
                amount=fees,
            )
            if ads:
                FeeLine.objects.create(
                    order=order,
                    fee_type=FeeLine.FeeType.OFFSITE_ADS,
                    amount=ads,
                )
        self.stdout.write(self.style.SUCCESS("Demo login: demo / demo12345"))
