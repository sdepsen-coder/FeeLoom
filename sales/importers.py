import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import FeeLine, ImportBatch, Order, OrderItem, ProductCost


MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 20000
MONEY = Decimal("0.01")


class CSVImportError(ValueError):
    pass


def normalized_header(value):
    return " ".join((value or "").replace("\ufeff", "").strip().lower().split())


def row_value(row, *aliases, default=""):
    normalized = {normalized_header(key): value for key, value in row.items() if key}
    for alias in aliases:
        value = normalized.get(normalized_header(alias))
        if value not in (None, ""):
            return str(value).strip()
    return default


def decimal_value(value, default=Decimal("0")):
    text = str(value or "").strip()
    if not text:
        return default
    negative = text.startswith("(") and text.endswith(")")
    cleaned = "".join(char for char in text if char.isdigit() or char in ".,-")
    if cleaned.count(",") and not cleaned.count("."):
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CSVImportError(f"Invalid amount: {value}") from exc
    return -number if negative else number


def integer_value(value, default=1):
    try:
        return max(1, int(decimal_value(value)))
    except (CSVImportError, ValueError):
        return default


def date_value(value):
    text = str(value or "").strip()
    formats = (
        "%m/%d/%y",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%y %H:%M",
        "%m/%d/%Y %H:%M",
    )
    for format_string in formats:
        try:
            parsed = datetime.strptime(text, format_string)
            return timezone.make_aware(parsed, timezone.get_current_timezone())
        except ValueError:
            continue
    raise CSVImportError(f"Invalid sale date: {value}")


def decode_csv(uploaded_file):
    if uploaded_file.size > MAX_CSV_BYTES:
        raise CSVImportError("The CSV file must be 5 MB or smaller.")
    raw = uploaded_file.read()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CSVImportError("The CSV file encoding is not supported.")


def import_etsy_orders(shop, uploaded_file):
    content = decode_csv(uploaded_file)
    reader = csv.DictReader(io.StringIO(content), strict=True)
    headers = {normalized_header(header) for header in (reader.fieldnames or [])}
    if "order id" not in headers or "sale date" not in headers:
        raise CSVImportError("Choose Etsy's Orders CSV file. Required columns are missing.")
    if "order value" not in headers and "order total" not in headers:
        raise CSVImportError("The CSV needs an Order Value or Order Total column.")

    try:
        rows = list(reader)
    except csv.Error as exc:
        raise CSVImportError("The CSV file is malformed and could not be read.") from exc

    batch = ImportBatch.objects.create(shop=shop, file_name=uploaded_file.name[:255])
    errors = []
    imported = 0
    skipped = 0
    total = 0

    for row_number, row in enumerate(rows, start=2):
        total += 1
        if total > MAX_CSV_ROWS:
            errors.append("Only the first 20,000 rows were processed.")
            break
        try:
            with transaction.atomic():
                order_id = row_value(row, "Order ID")
                if not order_id:
                    raise CSVImportError("Order ID is empty")
                if len(order_id) > 120:
                    raise CSVImportError("Order ID is longer than 120 characters")
                if Order.objects.filter(shop=shop, external_order_id=order_id).exists():
                    skipped += 1
                    continue

                ordered_at = date_value(row_value(row, "Sale Date"))
                currency = row_value(row, "Currency", default=shop.currency).upper()[:3]
                item_revenue = decimal_value(
                    row_value(row, "Order Value", "Order Total")
                )
                shipping_revenue = decimal_value(row_value(row, "Shipping"))
                discounts = abs(decimal_value(row_value(row, "Discount Amount")))
                refunds = abs(decimal_value(row_value(row, "Refund Amount")))
                processing_fee = abs(
                    decimal_value(row_value(row, "Card Processing Fees"))
                )
                quantity = integer_value(row_value(row, "Number of Items", "Quantity"))
                sku = row_value(row, "SKU").split(",")[0].strip().upper()
                if len(sku) > 120:
                    raise CSVImportError("SKU is longer than 120 characters")
                title = row_value(row, "Item Name", default=f"Etsy order {order_id}")[:255]
                gross = item_revenue + shipping_revenue - discounts - refunds
                transaction_fee = (gross * Decimal("0.065")).quantize(
                    MONEY,
                    rounding=ROUND_HALF_UP,
                )
                listing_fee = (Decimal("0.20") * quantity).quantize(MONEY)
                product_cost = ProductCost.objects.filter(shop=shop, sku=sku).first()

                order = Order(
                    shop=shop,
                    import_batch=batch,
                    external_order_id=order_id,
                    ordered_at=ordered_at,
                    currency=currency or shop.currency,
                    item_revenue=item_revenue,
                    shipping_revenue=shipping_revenue,
                    discounts=discounts,
                    refunds=refunds,
                    marketplace_fees=transaction_fee + listing_fee + processing_fee,
                    product_cost=(product_cost.unit_cost * quantity) if product_cost else 0,
                )
                costs_complete = bool(product_cost) and shipping_revenue == 0
                order.calculate_profit(costs_complete=costs_complete)
                order.save()
                OrderItem.objects.create(
                    order=order,
                    sku=sku,
                    title=title,
                    quantity=quantity,
                    unit_price=(item_revenue / quantity).quantize(MONEY),
                )
                FeeLine.objects.bulk_create(
                    [
                        FeeLine(
                            order=order,
                            fee_type=FeeLine.FeeType.TRANSACTION,
                            amount=transaction_fee,
                            description="Etsy transaction fee estimate",
                        ),
                        FeeLine(
                            order=order,
                            fee_type=FeeLine.FeeType.LISTING,
                            amount=listing_fee,
                            description="Etsy listing fee estimate",
                        ),
                        FeeLine(
                            order=order,
                            fee_type=FeeLine.FeeType.PAYMENT,
                            amount=processing_fee,
                            description="Card processing fee from Etsy CSV",
                        ),
                    ]
                )
                imported += 1
        except (CSVImportError, ValueError, TypeError) as exc:
            if len(errors) < 20:
                errors.append(f"Row {row_number}: {exc}")

    batch.total_rows = total
    batch.imported_rows = imported
    batch.skipped_rows = skipped
    batch.status = ImportBatch.Status.COMPLETED if imported or skipped else ImportBatch.Status.FAILED
    batch.error_message = "\n".join(errors)
    batch.save()
    return batch, errors
