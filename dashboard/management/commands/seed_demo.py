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

        jewelry_shop, _ = Shop.objects.get_or_create(
            workspace=workspace,
            name="Luna Forge Jewelry",
        )
        jewelry_products = [
            ("LFJ-RING-01", "Hammered Gold Stacking Ring", Decimal("38.00"), Decimal("9.40")),
            ("LFJ-NECK-02", "Celestial Moon Necklace", Decimal("52.00"), Decimal("14.25")),
            ("LFJ-EARR-03", "Pearl Drop Earrings", Decimal("44.00"), Decimal("11.60")),
            ("LFJ-BRAC-04", "Personalized Birthstone Bracelet", Decimal("48.00"), Decimal("13.10")),
            ("LFJ-HOOP-05", "Minimalist Silver Hoops", Decimal("32.00"), Decimal("7.80")),
        ]
        for sku, title, _, unit_cost in jewelry_products:
            ProductCost.objects.update_or_create(
                shop=jewelry_shop,
                sku=sku,
                defaults={
                    "title": title,
                    "materials": unit_cost - Decimal("4.50"),
                    "packaging": Decimal("1.25"),
                    "labor": Decimal("2.50"),
                    "overhead": Decimal("0.75"),
                },
            )

        for index in range(20):
            sku, title, unit_price, unit_cost = jewelry_products[index % len(jewelry_products)]
            quantity = 2 if index in {3, 10, 17} else 1
            item_revenue = unit_price * quantity
            discount = Decimal("5.00") if index in {5, 12} else Decimal("0")
            shipping_revenue = Decimal("4.50") if index % 4 == 0 else Decimal("0")
            shipping_cost = Decimal("5.25") + Decimal(index % 3)
            marketplace_fees = (item_revenue * Decimal("0.095") + Decimal("0.20") * quantity).quantize(Decimal("0.01"))
            ad_fees = (item_revenue * Decimal("0.12")).quantize(Decimal("0.01")) if index % 6 == 0 else Decimal("0")
            order, _ = Order.objects.update_or_create(
                shop=jewelry_shop,
                external_order_id=f"LUNA-{2020 - index}",
                defaults={
                    "ordered_at": timezone.now() - timedelta(hours=index * 19 + 3),
                    "item_revenue": item_revenue,
                    "shipping_revenue": shipping_revenue,
                    "discounts": discount,
                    "marketplace_fees": marketplace_fees,
                    "ad_fees": ad_fees,
                    "shipping_cost": shipping_cost,
                    "product_cost": unit_cost * quantity,
                },
            )
            order.calculate_profit()
            order.save()
            OrderItem.objects.update_or_create(
                order=order,
                sku=sku,
                defaults={
                    "title": title,
                    "quantity": quantity,
                    "unit_price": unit_price,
                },
            )
            FeeLine.objects.filter(order=order).delete()
            FeeLine.objects.create(
                order=order,
                fee_type=FeeLine.FeeType.TRANSACTION,
                amount=marketplace_fees,
            )
            if ad_fees:
                FeeLine.objects.create(
                    order=order,
                    fee_type=FeeLine.FeeType.OFFSITE_ADS,
                    amount=ad_fees,
                )
        self.stdout.write(self.style.SUCCESS("Demo login: demo / demo12345"))
