from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from sales.models import FeeLine, Order, OrderItem, ProductCost

from .etsy import active_access_token, get_receipt_payments, get_receipts
from .models import EtsySyncRun


def etsy_money(value):
    value = value or {}
    divisor = Decimal(str(value.get("divisor") or 100))
    return Decimal(str(value.get("amount") or 0)) / divisor


def sync_connection(connection):
    run = EtsySyncRun.objects.create(connection=connection)
    try:
        access_token = active_access_token(connection)
        since = connection.last_synced_at - timedelta(days=1) if connection.last_synced_at else None
        receipts = get_receipts(connection.shop.external_shop_id, access_token, min_last_modified=since)
        for receipt in receipts:
            created = sync_receipt(connection, receipt, access_token)
            if created:
                run.imported_orders += 1
            else:
                run.updated_orders += 1
        connection.last_synced_at = timezone.now()
        connection.last_error = ""
        connection.save(update_fields=["last_synced_at", "last_error", "updated_at"])
        run.status = EtsySyncRun.Status.SUCCESS
    except Exception as exc:
        connection.last_error = str(exc)[:1000]
        connection.save(update_fields=["last_error", "updated_at"])
        run.status = EtsySyncRun.Status.FAILED
        run.error_message = str(exc)[:2000]
        raise
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "imported_orders", "updated_orders", "error_message", "finished_at"])
    return run


@transaction.atomic
def sync_receipt(connection, receipt, access_token):
    shop = connection.shop
    receipt_id = str(receipt["receipt_id"])
    transactions = receipt.get("transactions") or []
    item_revenue = etsy_money(receipt.get("subtotal") or receipt.get("total_price"))
    shipping_revenue = etsy_money(receipt.get("total_shipping_cost"))
    discounts = abs(etsy_money(receipt.get("discount_amt")))
    currency = (receipt.get("grandtotal") or receipt.get("subtotal") or {}).get("currency_code", shop.currency)
    payments = get_receipt_payments(shop.external_shop_id, receipt_id, access_token)
    marketplace_fees = sum((etsy_money(payment.get("amount_fees")) for payment in payments), Decimal("0"))
    product_cost = Decimal("0")
    costs_complete = bool(transactions)
    for item in transactions:
        sku = str(item.get("sku") or "").strip().upper()
        quantity = max(1, int(item.get("quantity") or 1))
        saved_cost = ProductCost.objects.filter(shop=shop, sku=sku).first()
        if saved_cost:
            product_cost += saved_cost.unit_cost * quantity
        else:
            costs_complete = False
    ordered_at = datetime.fromtimestamp(
        int(receipt.get("create_timestamp") or receipt.get("created_timestamp")), tz=UTC
    )
    order, created = Order.objects.update_or_create(
        shop=shop,
        external_order_id=f"ETSY-{receipt_id}",
        defaults={
            "ordered_at": ordered_at,
            "currency": currency,
            "item_revenue": item_revenue,
            "shipping_revenue": shipping_revenue,
            "discounts": discounts,
            "marketplace_fees": marketplace_fees,
            "product_cost": product_cost,
        },
    )
    order.calculate_profit(costs_complete=costs_complete and shipping_revenue == 0)
    order.save()
    order.items.all().delete()
    for item in transactions:
        quantity = max(1, int(item.get("quantity") or 1))
        price = etsy_money(item.get("price")) or (item_revenue / max(1, len(transactions)) / quantity)
        OrderItem.objects.create(
            order=order,
            sku=str(item.get("sku") or "").strip().upper()[:120],
            title=str(item.get("title") or f"Etsy item {item.get('transaction_id', '')}")[:255],
            quantity=quantity,
            unit_price=price,
        )
    order.fee_lines.all().delete()
    if marketplace_fees:
        FeeLine.objects.create(
            order=order,
            fee_type=FeeLine.FeeType.PAYMENT,
            amount=marketplace_fees,
            description="Fees reported by Etsy Payments",
        )
    return created
