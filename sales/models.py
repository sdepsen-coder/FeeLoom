from decimal import Decimal

from django.db import models

from workspaces.models import Shop


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="imports")
    file_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    total_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    skipped_rows = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self):
        return self.file_name


class ProductCost(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="product_costs")
    sku = models.CharField(max_length=120)
    title = models.CharField(max_length=255, blank=True)
    materials = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    packaging = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    labor = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    overhead = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sku",)
        constraints = [
            models.UniqueConstraint(
                fields=("shop", "sku"),
                name="unique_product_cost_per_shop",
            ),
        ]

    @property
    def unit_cost(self):
        return self.materials + self.packaging + self.labor + self.overhead

    def __str__(self):
        return self.sku


class Order(models.Model):
    class ProfitStatus(models.TextChoices):
        PROFITABLE = "profitable", "Profitable"
        LOW_MARGIN = "low_margin", "Low margin"
        LOSS = "loss", "Loss"
        INCOMPLETE = "incomplete", "Cost incomplete"

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="orders")
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        related_name="orders",
        null=True,
        blank=True,
    )
    external_order_id = models.CharField(max_length=120)
    ordered_at = models.DateTimeField()
    currency = models.CharField(max_length=3, default="USD")
    item_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discounts = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refunds = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    marketplace_fees = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ad_fees = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    product_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_profit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    margin_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    profit_status = models.CharField(
        max_length=20,
        choices=ProfitStatus.choices,
        default=ProfitStatus.INCOMPLETE,
    )
    calculation_version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-ordered_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("shop", "external_order_id"),
                name="unique_external_order_per_shop",
            ),
        ]

    @property
    def gross_sales(self):
        return self.item_revenue + self.shipping_revenue - self.discounts - self.refunds

    @property
    def total_fees(self):
        return self.marketplace_fees + self.ad_fees

    def calculate_profit(self, costs_complete=True):
        gross = self.gross_sales
        self.net_profit = gross - self.total_fees - self.shipping_cost - self.product_cost
        self.margin_percent = (
            (self.net_profit / gross * Decimal("100")) if gross else Decimal("0")
        )
        if not costs_complete:
            self.profit_status = self.ProfitStatus.INCOMPLETE
        elif self.net_profit < 0:
            self.profit_status = self.ProfitStatus.LOSS
        elif self.margin_percent < Decimal("15"):
            self.profit_status = self.ProfitStatus.LOW_MARGIN
        else:
            self.profit_status = self.ProfitStatus.PROFITABLE
        return self.net_profit

    def __str__(self):
        return self.external_order_id


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    sku = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return self.title


class FeeLine(models.Model):
    class FeeType(models.TextChoices):
        LISTING = "listing", "Listing"
        TRANSACTION = "transaction", "Transaction"
        PAYMENT = "payment", "Payment processing"
        ETSY_ADS = "etsy_ads", "Etsy Ads"
        OFFSITE_ADS = "offsite_ads", "Offsite Ads"
        REGULATORY = "regulatory", "Regulatory"
        OTHER = "other", "Other"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="fee_lines")
    fee_type = models.CharField(max_length=30, choices=FeeType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.get_fee_type_display()}: {self.amount}"
