import csv
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from sales.importers import CSVImportError, import_etsy_orders
from sales.models import ImportBatch, Order, ProductCost
from workspaces.models import Membership, Shop
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY, ALL_SHOPS_VALUE, shop_selection

from .forms import EtsyCSVImportForm, ProductCostForm, ShopForm


def money(value):
    return value or Decimal("0")


def csv_safe(value):
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def workspace_context(request):
    membership, shops, shop, all_shops = shop_selection(request)
    return {"membership": membership, "shops": shops, "shop": shop, "all_shops": all_shops}


def selected_orders(context):
    if not context["membership"]:
        return Order.objects.none()
    if context["all_shops"]:
        return Order.objects.filter(shop__in=context["shops"])
    if context["shop"]:
        return Order.objects.filter(shop=context["shop"])
    return Order.objects.none()


def filtered_sales(request, context):
    orders = selected_orders(context).select_related("shop").prefetch_related("items")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if query:
        orders = orders.filter(
            Q(external_order_id__icontains=query)
            | Q(items__title__icontains=query)
            | Q(items__sku__icontains=query)
            | Q(shop__name__icontains=query)
        ).distinct()
    if status in Order.ProfitStatus.values:
        orders = orders.filter(profit_status=status)
    return orders, query, status


@login_required
def switch_shop(request):
    if request.method != "POST":
        return redirect("dashboard")
    context = workspace_context(request)
    if not context["membership"]:
        raise PermissionDenied
    selection = request.POST.get("shop", "")
    if selection == ALL_SHOPS_VALUE:
        request.session[ACTIVE_SHOP_SESSION_KEY] = ALL_SHOPS_VALUE
    else:
        shop = get_object_or_404(
            Shop,
            id=selection,
            workspace=context["membership"].workspace,
            is_active=True,
        )
        request.session[ACTIVE_SHOP_SESSION_KEY] = shop.id
    next_url = request.POST.get("next", reverse("dashboard"))
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("dashboard")
    return redirect(next_url)


@login_required
def dashboard(request):
    context = workspace_context(request)
    orders = selected_orders(context)
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
    net_profit = money(totals["net_profit"])
    context.update(
        {
            "display_currency": context["shop"].currency if context["shop"] else context["membership"].workspace.currency,
            "gross_sales": gross_sales,
            "total_fees": money(totals["marketplace_fees"]) + money(totals["ad_fees"]),
            "net_profit": net_profit,
            "margin": net_profit / gross_sales * 100 if gross_sales else Decimal("0"),
            "loss_count": orders.filter(profit_status=Order.ProfitStatus.LOSS).count(),
            "recent_orders": orders.select_related("shop").prefetch_related("items")[:8],
        }
    )
    return render(request, "dashboard/home.html", context)


@login_required
def sales_table(request):
    context = workspace_context(request)
    orders, query, status = filtered_sales(request, context)
    context.update(
        {"orders": orders, "query": query, "selected_status": status, "profit_statuses": Order.ProfitStatus.choices}
    )
    return render(request, "dashboard/sales.html", context)


@login_required
def export_sales(request):
    context = workspace_context(request)
    orders, _, _ = filtered_sales(request, context)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="feeloom-sales.csv"'
    writer = csv.writer(response)
    writer.writerow(
        ["Shop", "Order date", "Order ID", "Gross sales", "Marketplace fees", "Ad fees", "Shipping cost", "Product cost", "Net profit", "Margin percent", "Currency", "Status"]
    )
    for order in orders:
        writer.writerow(
            [csv_safe(order.shop.name), order.ordered_at.isoformat(), csv_safe(order.external_order_id), order.gross_sales, order.marketplace_fees, order.ad_fees, order.shipping_cost, order.product_cost, order.net_profit, order.margin_percent, order.currency, order.profit_status]
        )
    return response


@login_required
def sale_detail(request, order_id):
    context = workspace_context(request)
    context["order"] = get_object_or_404(
        selected_orders(context).prefetch_related("items", "fee_lines"), id=order_id
    )
    return render(request, "dashboard/sale_detail.html", context)


@login_required
def product_costs(request):
    context = workspace_context(request)
    shop = context["shop"]
    form = ProductCostForm(request.POST or None)
    if request.method == "POST":
        if not shop:
            form.add_error(None, "Choose one shop before saving product costs.")
        elif form.is_valid():
            cost, _ = ProductCost.objects.update_or_create(
                shop=shop,
                sku=form.cleaned_data["sku"].strip().upper(),
                defaults={field: form.cleaned_data[field] for field in ("title", "materials", "packaging", "labor", "overhead")},
            )
            return redirect(f"{request.path}?saved={cost.id}")
    costs = (
        ProductCost.objects.filter(shop__in=context["shops"]).select_related("shop")
        if context["all_shops"]
        else ProductCost.objects.filter(shop=shop)
    )
    context.update({"form": form, "costs": costs})
    return render(request, "dashboard/costs.html", context)


@login_required
def import_sales(request):
    context = workspace_context(request)
    form = EtsyCSVImportForm(
        request.POST or None,
        request.FILES or None,
        shops=context["shops"],
        selected_shop=context["shop"],
    )
    import_error = ""
    if request.method == "POST" and form.is_valid():
        try:
            batch, _ = import_etsy_orders(form.cleaned_data["shop"], form.cleaned_data["csv_file"])
            return redirect(f"{request.path}?batch={batch.id}")
        except CSVImportError as exc:
            import_error = str(exc)
    selected_batch = None
    batch_id = request.GET.get("batch")
    if batch_id and batch_id.isdigit():
        selected_batch = ImportBatch.objects.filter(shop__in=context["shops"], id=batch_id).first()
    imports = ImportBatch.objects.filter(shop__in=context["shops"]).select_related("shop")[:10]
    context.update({"form": form, "import_error": import_error, "selected_batch": selected_batch, "imports": imports})
    return render(request, "dashboard/import_sales.html", context)


def can_manage_shops(membership):
    return membership and membership.role in {Membership.Role.OWNER, Membership.Role.MANAGER}


@login_required
def shops(request):
    context = workspace_context(request)
    form = ShopForm(request.POST or None)
    if request.method == "POST":
        if not can_manage_shops(context["membership"]):
            raise PermissionDenied
        if form.is_valid():
            shop = form.save(commit=False)
            shop.workspace = context["membership"].workspace
            shop.save()
            request.session[ACTIVE_SHOP_SESSION_KEY] = shop.id
            return redirect("shops")
    context.update(
        {
            "form": form,
            "can_manage_shops": can_manage_shops(context["membership"]),
            "workspace_shops": context["membership"].workspace.shops.all() if context["membership"] else [],
        }
    )
    return render(request, "dashboard/shops.html", context)


@login_required
def toggle_shop(request, shop_id):
    if request.method != "POST":
        return redirect("shops")
    context = workspace_context(request)
    if not can_manage_shops(context["membership"]):
        raise PermissionDenied
    shop = get_object_or_404(Shop, id=shop_id, workspace=context["membership"].workspace)
    if shop.is_active and context["membership"].workspace.shops.filter(is_active=True).count() == 1:
        return redirect("shops")
    shop.is_active = not shop.is_active
    shop.save(update_fields=["is_active"])
    if str(request.session.get(ACTIVE_SHOP_SESSION_KEY)) == str(shop.id) and not shop.is_active:
        request.session[ACTIVE_SHOP_SESSION_KEY] = ALL_SHOPS_VALUE
    return redirect("shops")
