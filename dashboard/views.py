import csv
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from sales.models import Order, ProductCost
from workspaces.selectors import current_membership, current_shop

from .forms import ProductCostForm


def money(value):
    return value or Decimal("0")


def workspace_context(request):
    membership = current_membership(request.user)
    shop = current_shop(request.user)
    return membership, shop


def filtered_sales(request, shop):
    orders = (
        Order.objects.filter(shop=shop).prefetch_related("items")
        if shop
        else Order.objects.none()
    )
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if query:
        orders = orders.filter(
            Q(external_order_id__icontains=query)
            | Q(items__title__icontains=query)
            | Q(items__sku__icontains=query)
        ).distinct()
    if status in Order.ProfitStatus.values:
        orders = orders.filter(profit_status=status)
    return orders, query, status


@login_required
def dashboard(request):
    membership, shop = workspace_context(request)
    orders = Order.objects.filter(shop=shop) if shop else Order.objects.none()
    totals = orders.aggregate(
        item_revenue=Sum("item_revenue"),
        shipping_revenue=Sum("shipping_revenue"),
        discounts=Sum("discounts"),
        refunds=Sum("refunds"),
        marketplace_fees=Sum("marketplace_fees"),
        ad_fees=Sum("ad_fees"),
        net_profit=Sum("net_profit"),
    )
    gross_sales = (
        money(totals["item_revenue"])
        + money(totals["shipping_revenue"])
        - money(totals["discounts"])
        - money(totals["refunds"])
    )
    total_fees = money(totals["marketplace_fees"]) + money(totals["ad_fees"])
    net_profit = money(totals["net_profit"])
    margin = net_profit / gross_sales * 100 if gross_sales else Decimal("0")
    return render(
        request,
        "dashboard/home.html",
        {
            "membership": membership,
            "shop": shop,
            "gross_sales": gross_sales,
            "total_fees": total_fees,
            "net_profit": net_profit,
            "margin": margin,
            "loss_count": orders.filter(profit_status=Order.ProfitStatus.LOSS).count(),
            "recent_orders": orders.select_related("shop").prefetch_related("items")[:8],
        },
    )


@login_required
def sales_table(request):
    membership, shop = workspace_context(request)
    orders, query, status = filtered_sales(request, shop)
    return render(
        request,
        "dashboard/sales.html",
        {
            "membership": membership,
            "shop": shop,
            "orders": orders,
            "query": query,
            "selected_status": status,
            "profit_statuses": Order.ProfitStatus.choices,
        },
    )


@login_required
def export_sales(request):
    _, shop = workspace_context(request)
    orders, _, _ = filtered_sales(request, shop)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="feeloom-sales.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Order date",
            "Order ID",
            "Gross sales",
            "Marketplace fees",
            "Ad fees",
            "Shipping cost",
            "Product cost",
            "Net profit",
            "Margin percent",
            "Currency",
            "Status",
        ]
    )
    for order in orders:
        writer.writerow(
            [
                order.ordered_at.isoformat(),
                order.external_order_id,
                order.gross_sales,
                order.marketplace_fees,
                order.ad_fees,
                order.shipping_cost,
                order.product_cost,
                order.net_profit,
                order.margin_percent,
                order.currency,
                order.profit_status,
            ]
        )
    return response


@login_required
def sale_detail(request, order_id):
    membership, shop = workspace_context(request)
    order = get_object_or_404(
        Order.objects.prefetch_related("items", "fee_lines"),
        id=order_id,
        shop=shop,
    )
    return render(
        request,
        "dashboard/sale_detail.html",
        {"membership": membership, "shop": shop, "order": order},
    )


@login_required
def product_costs(request):
    membership, shop = workspace_context(request)
    form = ProductCostForm(request.POST or None)
    if request.method == "POST" and shop and form.is_valid():
        cost, _ = ProductCost.objects.update_or_create(
            shop=shop,
            sku=form.cleaned_data["sku"].strip().upper(),
            defaults={
                "title": form.cleaned_data["title"],
                "materials": form.cleaned_data["materials"],
                "packaging": form.cleaned_data["packaging"],
                "labor": form.cleaned_data["labor"],
                "overhead": form.cleaned_data["overhead"],
            },
        )
        return redirect(f"{request.path}?saved={cost.id}")
    costs = ProductCost.objects.filter(shop=shop) if shop else ProductCost.objects.none()
    return render(
        request,
        "dashboard/costs.html",
        {"membership": membership, "shop": shop, "form": form, "costs": costs},
    )
