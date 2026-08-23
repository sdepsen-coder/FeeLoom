import csv
import json
import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import sentry_sdk
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.core.serializers.json import DjangoJSONEncoder
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.forms import BetaInviteForm
from accounts.models import BetaInvite, LegalAcceptance
from integrations.models import EtsyConnection, EtsySyncRun
from sales.importers import CSVImportError, import_etsy_orders
from sales.models import FeeLine, ImportBatch, Order, OrderItem, ProductCost
from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY, ALL_SHOPS_VALUE, shop_selection

from .forms import DeleteWorkspaceForm, EtsyCSVImportForm, FeedbackForm, ProductCostForm, ShopForm, SystemEmailTestForm
from .management.commands.launch_check import production_checks, production_email_ready
from .models import AuditEvent, Feedback
from .audit import record_audit


logger = logging.getLogger(__name__)


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
    sort = request.GET.get("sort", "newest").strip()
    if query:
        orders = orders.filter(
            Q(external_order_id__icontains=query)
            | Q(items__title__icontains=query)
            | Q(items__sku__icontains=query)
            | Q(shop__name__icontains=query)
        ).distinct()
    if status in Order.ProfitStatus.values:
        orders = orders.filter(profit_status=status)
    sort_fields = {
        "newest": ("-ordered_at", "-id"),
        "oldest": ("ordered_at", "id"),
        "profit_high": ("-net_profit", "-ordered_at", "-id"),
        "profit_low": ("net_profit", "-ordered_at", "-id"),
    }
    if sort not in sort_fields:
        sort = "newest"
    return orders.order_by(*sort_fields[sort]), query, status, sort


def landing(request):
    return render(
        request,
        "landing.html",
        {"support_email": settings.FEELOOM_SUPPORT_EMAIL or "sdepsen@gmail.com"},
    )


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
    if not context["membership"]:
        context["support_email"] = settings.FEELOOM_SUPPORT_EMAIL
        return render(request, "dashboard/no_workspace.html", context)
    period = request.GET.get("period", "30")
    period_days = {"30": 30, "90": 90, "all": None}.get(period)
    if period_days is None and period != "all":
        period = "30"
        period_days = 30
    orders = selected_orders(context)
    if period_days:
        orders = orders.filter(ordered_at__gte=timezone.now() - timedelta(days=period_days))
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
    status_counts = dict(
        orders.values_list("profit_status").annotate(total=Count("id"))
    )
    trend_rows = list(
        orders.annotate(day=TruncDate("ordered_at"))
        .values("day")
        .annotate(net_profit=Sum("net_profit"))
        .order_by("day")
    )[-12:]
    trend_peak = max(
        (abs(money(row["net_profit"])) for row in trend_rows),
        default=Decimal("1"),
    ) or Decimal("1")
    profit_trend = [
        {
            "label": row["day"].strftime("%b %d"),
            "value": money(row["net_profit"]),
            "height": max(6, round(float(abs(money(row["net_profit"])) / trend_peak) * 100)),
            "is_loss": money(row["net_profit"]) < 0,
        }
        for row in trend_rows
    ]
    context.update(
        {
            "display_currency": context["shop"].currency if context["shop"] else context["membership"].workspace.currency,
            "gross_sales": gross_sales,
            "total_fees": money(totals["marketplace_fees"]) + money(totals["ad_fees"]),
            "net_profit": net_profit,
            "margin": net_profit / gross_sales * 100 if gross_sales else Decimal("0"),
            "loss_count": orders.filter(profit_status=Order.ProfitStatus.LOSS).count(),
            "order_count": orders.count(),
            "profitable_count": status_counts.get(Order.ProfitStatus.PROFITABLE, 0),
            "low_margin_count": status_counts.get(Order.ProfitStatus.LOW_MARGIN, 0),
            "incomplete_count": status_counts.get(Order.ProfitStatus.INCOMPLETE, 0),
            "profit_trend": profit_trend,
            "selected_period": period,
            "recent_orders": orders.select_related("shop").prefetch_related("items")[:8],
            "show_getting_started": not orders.exists(),
        }
    )
    return render(request, "dashboard/home.html", context)


@login_required
def sales_table(request):
    context = workspace_context(request)
    orders, query, status, sort = filtered_sales(request, context)
    totals = orders.aggregate(
        item_revenue=Sum("item_revenue"),
        shipping_revenue=Sum("shipping_revenue"),
        discounts=Sum("discounts"),
        refunds=Sum("refunds"),
        net_profit=Sum("net_profit"),
    )
    gross_sales = (
        money(totals["item_revenue"])
        + money(totals["shipping_revenue"])
        - money(totals["discounts"])
        - money(totals["refunds"])
    )
    net_profit = money(totals["net_profit"])
    sales_page = Paginator(orders, 25).get_page(request.GET.get("page"))
    filter_params = request.GET.copy()
    filter_params.pop("page", None)
    context.update(
        {
            "orders": sales_page.object_list,
            "sales_page": sales_page,
            "filter_query": filter_params.urlencode(),
            "query": query,
            "selected_status": status,
            "selected_sort": sort,
            "profit_statuses": Order.ProfitStatus.choices,
            "sort_options": (
                ("newest", "Newest first"),
                ("oldest", "Oldest first"),
                ("profit_high", "Highest profit"),
                ("profit_low", "Lowest profit"),
            ),
            "sales_gross": gross_sales,
            "sales_net_profit": net_profit,
            "sales_margin": net_profit / gross_sales * 100 if gross_sales else Decimal("0"),
            "display_currency": context["shop"].currency if context["shop"] else context["membership"].workspace.currency,
        }
    )
    return render(request, "dashboard/sales.html", context)


@login_required
def export_sales(request):
    context = workspace_context(request)
    orders, _, _, _ = filtered_sales(request, context)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="feeloom-sales.csv"'
    if context["membership"]:
        record_audit(
            request,
            workspace=context["membership"].workspace,
            shop=context["shop"],
            action="sales.exported",
            summary="Sales CSV downloaded",
        )
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
    workspace_orders = (
        Order.objects.filter(shop__workspace=context["membership"].workspace)
        if context["membership"]
        else Order.objects.none()
    )
    order = get_object_or_404(
        workspace_orders.select_related("shop").prefetch_related("items", "fee_lines"),
        id=order_id,
    )
    total_costs = order.total_fees + order.shipping_cost + order.product_cost
    context.update(
        {
            "order": order,
            "total_costs": total_costs,
            "fee_rate": order.total_fees / order.gross_sales * 100 if order.gross_sales else Decimal("0"),
            "item_quantity": sum(item.quantity for item in order.items.all()),
            "fee_lines": order.fee_lines.all(),
        }
    )
    return render(request, "dashboard/sale_detail.html", context)


@login_required
def product_costs(request):
    context = workspace_context(request)
    shop = context["shop"]
    query = request.GET.get("q", "").strip()
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
            record_audit(
                request,
                workspace=context["membership"].workspace,
                shop=shop,
                action="product_cost.saved",
                summary="Product cost saved",
            )
            return redirect(f"{request.path}?saved={cost.id}")
    costs = (
        ProductCost.objects.filter(shop__in=context["shops"]).select_related("shop")
        if context["all_shops"]
        else ProductCost.objects.filter(shop=shop)
    )
    if query:
        costs = costs.filter(Q(sku__icontains=query) | Q(title__icontains=query))
    costs = list(costs)
    cost_count = len(costs)
    cost_total = sum((cost.unit_cost for cost in costs), Decimal("0"))
    context.update(
        {
            "form": form,
            "costs": costs,
            "cost_query": query,
            "cost_count": cost_count,
            "cost_materials": sum((cost.materials for cost in costs), Decimal("0")),
            "cost_labor": sum((cost.labor for cost in costs), Decimal("0")),
            "average_unit_cost": cost_total / cost_count if cost_count else Decimal("0"),
        }
    )
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
            record_audit(
                request,
                workspace=context["membership"].workspace,
                shop=form.cleaned_data["shop"],
                action="sales.imported",
                summary="Sales CSV imported",
                metadata={
                    "imported_rows": batch.imported_rows,
                    "skipped_rows": batch.skipped_rows,
                },
            )
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


def is_workspace_owner(request, membership):
    return (
        membership
        and membership.role == Membership.Role.OWNER
        and membership.workspace.owner_id == request.user.id
    )


def can_manage_beta_invites(request, membership):
    return is_workspace_owner(request, membership) and request.user.is_staff


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
            record_audit(
                request,
                workspace=shop.workspace,
                shop=shop,
                action="shop.created",
                summary="Shop created",
            )
            request.session[ACTIVE_SHOP_SESSION_KEY] = shop.id
            return redirect("shops")
    workspace_shops = list(
        context["membership"].workspace.shops.select_related("etsy_connection").all()
    ) if context["membership"] else []
    shop_ids = [workspace_shop.id for workspace_shop in workspace_shops]
    order_summaries = {
        row["shop_id"]: row
        for row in Order.objects.filter(shop_id__in=shop_ids)
        .values("shop_id")
        .annotate(
            order_count=Count("id"),
            item_revenue=Sum("item_revenue"),
            shipping_revenue=Sum("shipping_revenue"),
            discounts=Sum("discounts"),
            refunds=Sum("refunds"),
            net_profit=Sum("net_profit"),
        )
    }
    cost_counts = {
        row["shop_id"]: row["cost_count"]
        for row in ProductCost.objects.filter(shop_id__in=shop_ids)
        .values("shop_id")
        .annotate(cost_count=Count("id"))
    }
    for workspace_shop in workspace_shops:
        summary = order_summaries.get(workspace_shop.id, {})
        workspace_shop.order_count = summary.get("order_count", 0)
        workspace_shop.cost_count = cost_counts.get(workspace_shop.id, 0)
        workspace_shop.gross_sales = (
            money(summary.get("item_revenue"))
            + money(summary.get("shipping_revenue"))
            - money(summary.get("discounts"))
            - money(summary.get("refunds"))
        )
        workspace_shop.net_profit = money(summary.get("net_profit"))
    context.update(
        {
            "form": form,
            "can_manage_shops": can_manage_shops(context["membership"]),
            "workspace_shops": workspace_shops,
            "shop_count": len(workspace_shops),
            "active_shop_count": sum(shop.is_active for shop in workspace_shops),
            "connected_shop_count": sum(
                hasattr(shop, "etsy_connection") and shop.etsy_connection.is_active
                for shop in workspace_shops
            ),
            "shop_order_count": sum(shop.order_count for shop in workspace_shops),
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
    record_audit(
        request,
        workspace=context["membership"].workspace,
        shop=shop,
        action="shop.activated" if shop.is_active else "shop.deactivated",
        summary="Shop status changed",
    )
    if str(request.session.get(ACTIVE_SHOP_SESSION_KEY)) == str(shop.id) and not shop.is_active:
        request.session[ACTIVE_SHOP_SESSION_KEY] = ALL_SHOPS_VALUE
    return redirect("shops")


@login_required
def feedback(request):
    context = workspace_context(request)
    if not context["membership"]:
        raise PermissionDenied
    source_page = request.GET.get("from", "")[:500]
    if not source_page.startswith("/") or source_page.startswith("//"):
        source_page = ""
    initial = {"page_path": source_page}
    form = FeedbackForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        submission = form.save(commit=False)
        submission.workspace = context["membership"].workspace
        submission.shop = context["shop"]
        submission.user = request.user
        submission.save()
        record_audit(
            request,
            workspace=context["membership"].workspace,
            shop=context["shop"],
            action="feedback.submitted",
            summary="Beta feedback submitted",
        )
        if settings.EMAIL_DELIVERY_ENABLED and settings.FEELOOM_SUPPORT_EMAIL:
            try:
                send_mail(
                    f"FeeLoom beta feedback: {submission.get_category_display()}",
                    "\n".join(
                        [
                            f"Workspace: {submission.workspace.name}",
                            f"Shop: {submission.shop.name if submission.shop else '-'}",
                            f"User: {request.user.username}",
                            f"Rating: {submission.rating}/5",
                            f"Page: {submission.page_path or '-'}",
                            "",
                            submission.message,
                        ]
                    ),
                    settings.DEFAULT_FROM_EMAIL,
                    [settings.FEELOOM_SUPPORT_EMAIL],
                    fail_silently=False,
                )
            except Exception:
                logger.exception("feedback_notification_failed")
        redirect_params = {"sent": "1"}
        if submission.page_path:
            redirect_params["from"] = submission.page_path
        return redirect(f"{reverse('feedback')}?{urlencode(redirect_params)}")
    context.update(
        {
            "form": form,
            "feedback_sent": request.GET.get("sent") == "1",
            "feedback_source_page": source_page,
        }
    )
    return render(request, "dashboard/feedback.html", context)


def privacy_context(request, context, *, delete_form=None):
    membership = context["membership"]
    workspace = membership.workspace
    owner = is_workspace_owner(request, membership)
    shops = workspace.shops.all()
    context.update(
        {
            "can_delete_workspace": owner,
            "delete_form": delete_form or DeleteWorkspaceForm(
                user=request.user,
                workspace=workspace,
            ) if owner else None,
            "privacy_stats": (
                {"label": "Shops", "value": shops.count()},
                {"label": "Orders", "value": Order.objects.filter(shop__workspace=workspace).count()},
                {"label": "Product costs", "value": ProductCost.objects.filter(shop__workspace=workspace).count()},
                {"label": "Imports", "value": ImportBatch.objects.filter(shop__workspace=workspace).count()},
            ),
            "privacy_inventory": (
                {"label": "Workspace members", "value": workspace.memberships.count()},
                {"label": "Etsy connections", "value": EtsyConnection.objects.filter(shop__workspace=workspace).count()},
                {"label": "Feedback entries", "value": workspace.feedback.count()},
                {"label": "Activity events", "value": workspace.audit_events.count()},
            ),
            "last_workspace_export": workspace.audit_events.filter(
                action="workspace.exported"
            ).first(),
        }
    )
    return context


@login_required
def privacy_data(request):
    context = workspace_context(request)
    if not context["membership"]:
        raise PermissionDenied
    privacy_context(request, context)
    return render(request, "dashboard/privacy_data.html", context)


@login_required
def export_workspace_data(request):
    context = workspace_context(request)
    membership = context["membership"]
    if not is_workspace_owner(request, membership):
        raise PermissionDenied
    workspace = membership.workspace
    shops = list(workspace.shops.order_by("id"))
    shop_ids = [shop.id for shop in shops]
    orders = list(Order.objects.filter(shop_id__in=shop_ids).order_by("id"))
    order_ids = [order.id for order in orders]
    connections = list(EtsyConnection.objects.filter(shop_id__in=shop_ids).order_by("id"))
    connection_ids = [connection.id for connection in connections]

    payload = {
        "exported_at": timezone.now(),
        "workspace": {
            "id": workspace.id,
            "name": workspace.name,
            "slug": workspace.slug,
            "currency": workspace.currency,
            "country_code": workspace.country_code,
            "created_at": workspace.created_at,
        },
        "memberships": list(
            workspace.memberships.order_by("id").values(
                "id", "user__username", "user__email", "role", "is_active", "created_at"
            )
        ),
        "legal_acceptances": list(
            LegalAcceptance.objects.filter(user=request.user).order_by("id").values(
                "id", "version", "accepted_at"
            )
        ),
        "beta_invites": list(
            BetaInvite.objects.filter(workspace=workspace).order_by("id").values(
                "id", "code", "label", "max_uses", "use_count", "expires_at", "is_active", "created_at"
            )
        ),
        "shops": [
            {
                "id": shop.id,
                "name": shop.name,
                "marketplace": shop.marketplace,
                "external_shop_id": shop.external_shop_id,
                "currency": shop.currency,
                "is_active": shop.is_active,
                "created_at": shop.created_at,
            }
            for shop in shops
        ],
        "product_costs": list(ProductCost.objects.filter(shop_id__in=shop_ids).order_by("id").values()),
        "orders": [
            {
                "id": order.id,
                "shop_id": order.shop_id,
                "import_batch_id": order.import_batch_id,
                "external_order_id": order.external_order_id,
                "ordered_at": order.ordered_at,
                "currency": order.currency,
                "item_revenue": order.item_revenue,
                "shipping_revenue": order.shipping_revenue,
                "discounts": order.discounts,
                "refunds": order.refunds,
                "shipping_cost": order.shipping_cost,
                "marketplace_fees": order.marketplace_fees,
                "ad_fees": order.ad_fees,
                "product_cost": order.product_cost,
                "net_profit": order.net_profit,
                "margin_percent": order.margin_percent,
                "profit_status": order.profit_status,
                "created_at": order.created_at,
                "updated_at": order.updated_at,
            }
            for order in orders
        ],
        "order_items": list(OrderItem.objects.filter(order_id__in=order_ids).order_by("id").values()),
        "fee_lines": list(FeeLine.objects.filter(order_id__in=order_ids).order_by("id").values()),
        "imports": list(ImportBatch.objects.filter(shop_id__in=shop_ids).order_by("id").values()),
        "etsy_connections": [
            {
                "id": connection.id,
                "shop_id": connection.shop_id,
                "etsy_user_id": connection.etsy_user_id,
                "scopes": connection.scopes,
                "connected_at": connection.connected_at,
                "updated_at": connection.updated_at,
                "last_synced_at": connection.last_synced_at,
                "last_error": connection.last_error,
                "is_active": connection.is_active,
            }
            for connection in connections
        ],
        "etsy_sync_runs": list(EtsySyncRun.objects.filter(connection_id__in=connection_ids).order_by("id").values()),
        "feedback": list(
            Feedback.objects.filter(workspace=workspace).order_by("id").values(
                "id", "shop_id", "user_id", "category", "rating", "message", "page_path", "status", "created_at"
            )
        ),
        "audit_events": list(
            workspace.audit_events.order_by("id").values(
                "id", "shop_id", "user_id", "action", "summary", "metadata", "request_id", "created_at"
            )
        ),
    }
    record_audit(
        request,
        workspace=workspace,
        action="workspace.exported",
        summary="Workspace data downloaded",
    )
    response = HttpResponse(
        json.dumps(payload, cls=DjangoJSONEncoder, indent=2),
        content_type="application/json",
    )
    response["Content-Disposition"] = f'attachment; filename="feeloom-{workspace.slug}-data.json"'
    return response


@login_required
def delete_workspace(request):
    if request.method != "POST":
        return redirect("privacy_data")
    context = workspace_context(request)
    membership = context["membership"]
    if not is_workspace_owner(request, membership):
        raise PermissionDenied
    workspace = membership.workspace
    form = DeleteWorkspaceForm(request.POST, user=request.user, workspace=workspace)
    if not form.is_valid():
        privacy_context(request, context, delete_form=form)
        return render(request, "dashboard/privacy_data.html", context, status=400)
    record_audit(
        request,
        workspace=workspace,
        action="workspace.deleted",
        summary="Workspace permanently deleted",
    )
    user = request.user
    workspace.delete()
    logout(request)
    if (
        not user.workspace_memberships.filter(is_active=True).exists()
        and not user.owned_workspaces.exists()
    ):
        user.delete()
    messages.success(request, "Your workspace, account, and FeeLoom data were permanently deleted.")
    return redirect("login")


@login_required
def beta_invites(request):
    context = workspace_context(request)
    membership = context["membership"]
    if not can_manage_beta_invites(request, membership):
        raise PermissionDenied
    form = BetaInviteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        invite = form.save_for(workspace=membership.workspace, user=request.user)
        record_audit(
            request,
            workspace=membership.workspace,
            action="beta_invite.created",
            summary="Beta invite created",
            metadata={"max_uses": invite.max_uses},
        )
        return redirect(f"{reverse('beta_invites')}?created={invite.id}")
    invites = list(membership.workspace.beta_invites.select_related("created_by"))
    for invite in invites:
        invite.signup_url = request.build_absolute_uri(
            f"{reverse('signup')}?invite={invite.code}"
        )
    created_id = request.GET.get("created", "")
    created_invite = next(
        (invite for invite in invites if str(invite.id) == created_id),
        None,
    )
    context.update(
        {
            "form": form,
            "invites": invites,
            "created_invite": created_invite,
            "invite_count": len(invites),
            "available_invite_count": sum(invite.is_available for invite in invites),
            "invite_use_count": sum(invite.use_count for invite in invites),
            "invite_remaining_uses": sum(
                max(invite.max_uses - invite.use_count, 0)
                for invite in invites
                if invite.is_available
            ),
        }
    )
    return render(request, "dashboard/beta_invites.html", context)


@login_required
def toggle_invite(request, invite_id):
    if request.method != "POST":
        return redirect("beta_invites")
    context = workspace_context(request)
    membership = context["membership"]
    if not can_manage_beta_invites(request, membership):
        raise PermissionDenied
    invite = get_object_or_404(BetaInvite, id=invite_id, workspace=membership.workspace)
    invite.is_active = not invite.is_active
    invite.save(update_fields=["is_active"])
    record_audit(
        request,
        workspace=membership.workspace,
        action="beta_invite.enabled" if invite.is_active else "beta_invite.disabled",
        summary="Beta invite status changed",
    )
    return redirect("beta_invites")


@login_required
def activity(request):
    context = workspace_context(request)
    membership = context["membership"]
    if not is_workspace_owner(request, membership):
        raise PermissionDenied
    query = request.GET.get("q", "").strip()
    selected_category = request.GET.get("category", "")
    selected_shop = request.GET.get("shop", "")
    categories = (
        ("shop.", "Shops"),
        ("etsy.", "Etsy connections"),
        ("sales.", "Sales and imports"),
        ("product_cost.", "Product costs"),
        ("workspace.", "Workspace data"),
        ("feedback.", "Feedback"),
        ("beta_invite.", "Beta invites"),
        ("system.", "System checks"),
    )
    valid_categories = {value for value, _label in categories}
    workspace_shops = membership.workspace.shops.order_by("name")
    events = AuditEvent.objects.filter(workspace=membership.workspace).select_related(
        "shop", "user"
    )
    if query:
        events = events.filter(
            Q(summary__icontains=query)
            | Q(action__icontains=query)
            | Q(user__username__icontains=query)
            | Q(request_id__icontains=query)
        )
    if selected_category in valid_categories:
        events = events.filter(action__startswith=selected_category)
    else:
        selected_category = ""
    if selected_shop.isdigit() and workspace_shops.filter(id=selected_shop).exists():
        events = events.filter(shop_id=selected_shop)
    else:
        selected_shop = ""
    filter_params = request.GET.copy()
    filter_params.pop("page", None)
    context.update(
        {
            "activity_page": Paginator(events, 25).get_page(request.GET.get("page")),
            "activity_count": events.count(),
            "activity_actor_count": events.exclude(user=None).values("user_id").distinct().count(),
            "activity_shop_count": events.exclude(shop=None).values("shop_id").distinct().count(),
            "latest_activity": events.first(),
            "activity_query": query,
            "activity_categories": categories,
            "selected_activity_category": selected_category,
            "activity_shops": workspace_shops,
            "selected_activity_shop": selected_shop,
            "activity_filter_query": filter_params.urlencode(),
        }
    )
    return render(request, "dashboard/activity.html", context)


@login_required
def system_status(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    context = workspace_context(request)
    action = request.POST.get("action", "email_test")
    form = SystemEmailTestForm(
        request.POST
        if request.method == "POST" and action == "email_test"
        else None
    )
    if request.method == "POST" and action == "sentry_test":
        if not settings.SENTRY_DSN:
            messages.error(request, "Sentry error monitoring is not configured yet.")
        else:
            try:
                sentry_sdk.capture_message(
                    "FeeLoom production monitoring test",
                    level="error",
                )
                sentry_sdk.flush(timeout=5)
                messages.success(request, "Sentry test event sent successfully.")
            except Exception:
                logger.exception("production_sentry_test_failed")
                messages.error(request, "The Sentry test event could not be sent.")
        return redirect("system_status")
    if request.method == "POST" and action == "email_test" and form.is_valid():
        if not production_email_ready():
            messages.error(request, "Production email delivery is not configured yet.")
        else:
            try:
                send_mail(
                    "FeeLoom email delivery test",
                    "FeeLoom account email delivery is configured and working.",
                    settings.DEFAULT_FROM_EMAIL,
                    [form.cleaned_data["recipient"]],
                    fail_silently=False,
                )
                if context["membership"]:
                    record_audit(
                        request,
                        workspace=context["membership"].workspace,
                        action="system.email_test_succeeded",
                        summary="Production email delivery tested",
                    )
                messages.success(request, "Test email sent successfully.")
            except Exception:
                logger.exception("production_email_test_failed")
                messages.error(
                    request,
                    "The test email could not be sent. Check the provider and Render logs.",
                )
        return redirect("system_status")
    context.update(
        {
            "readiness_checks": production_checks(),
            "email_test_form": form,
            "email_delivery_enabled": production_email_ready(),
            "sentry_enabled": bool(settings.SENTRY_DSN),
        }
    )
    return render(request, "dashboard/system_status.html", context)


@login_required
def feedback_inbox(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    context = workspace_context(request)
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    category_filter = request.GET.get("category", "")
    rating_filter = request.GET.get("rating", "")
    valid_statuses = {value for value, _label in Feedback.Status.choices}
    valid_categories = {value for value, _label in Feedback.Category.choices}
    all_feedback = Feedback.objects.select_related("workspace", "shop", "user")
    feedback_items = all_feedback
    if query:
        feedback_items = feedback_items.filter(
            Q(workspace__name__icontains=query)
            | Q(shop__name__icontains=query)
            | Q(user__username__icontains=query)
            | Q(user__email__icontains=query)
            | Q(message__icontains=query)
            | Q(page_path__icontains=query)
        )
    if status_filter in valid_statuses:
        feedback_items = feedback_items.filter(status=status_filter)
    else:
        status_filter = ""
    if category_filter in valid_categories:
        feedback_items = feedback_items.filter(category=category_filter)
    else:
        category_filter = ""
    if rating_filter in {str(value) for value in range(1, 6)}:
        feedback_items = feedback_items.filter(rating=int(rating_filter))
    else:
        rating_filter = ""
    filter_params = request.GET.copy()
    filter_params.pop("page", None)
    context.update(
        {
            "feedback_page": Paginator(feedback_items, 25).get_page(request.GET.get("page")),
            "feedback_match_count": feedback_items.count(),
            "feedback_total_count": all_feedback.count(),
            "feedback_new_count": all_feedback.filter(status=Feedback.Status.NEW).count(),
            "feedback_reviewing_count": all_feedback.filter(status=Feedback.Status.REVIEWING).count(),
            "feedback_resolved_count": all_feedback.filter(status=Feedback.Status.RESOLVED).count(),
            "feedback_average_rating": all_feedback.aggregate(value=Avg("rating"))["value"] or 0,
            "feedback_statuses": Feedback.Status.choices,
            "feedback_categories": Feedback.Category.choices,
            "feedback_ratings": range(1, 6),
            "feedback_query": query,
            "status_filter": status_filter,
            "category_filter": category_filter,
            "rating_filter": rating_filter,
            "feedback_filter_query": filter_params.urlencode(),
        }
    )
    return render(request, "dashboard/feedback_inbox.html", context)


@login_required
def beta_accounts(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    context = workspace_context(request)
    query = request.GET.get("q", "").strip()
    verification = request.GET.get("verification", "")
    workspaces = Workspace.objects.select_related("owner").annotate(
        shop_count=Count("shops", distinct=True),
        order_count=Count("shops__orders", distinct=True),
        cost_count=Count("shops__product_costs", distinct=True),
        feedback_count=Count("feedback", distinct=True),
    ).order_by("-created_at", "-id")
    if query:
        workspaces = workspaces.filter(
            Q(name__icontains=query)
            | Q(owner__username__icontains=query)
            | Q(owner__email__icontains=query)
        )
    if verification == "verified":
        workspaces = workspaces.filter(owner__is_active=True)
    elif verification == "unverified":
        workspaces = workspaces.filter(owner__is_active=False)
    else:
        verification = ""
    context.update(
        {
            "accounts_page": Paginator(workspaces, 25).get_page(request.GET.get("page")),
            "account_query": query,
            "verification_filter": verification,
        }
    )
    return render(request, "dashboard/beta_accounts.html", context)


@login_required
def update_feedback_status(request, feedback_id):
    if not request.user.is_superuser:
        raise PermissionDenied
    if request.method != "POST":
        return redirect("feedback_inbox")
    submission = get_object_or_404(Feedback, id=feedback_id)
    new_status = request.POST.get("status", "")
    valid_statuses = {value for value, _label in Feedback.Status.choices}
    if new_status in valid_statuses:
        submission.status = new_status
        submission.save(update_fields=["status"])
        messages.success(request, "Feedback status updated.")
    else:
        messages.error(request, "Choose a valid feedback status.")
    redirect_params = {}
    status_filter = request.POST.get("status_filter", "")
    if status_filter in valid_statuses:
        redirect_params["status"] = status_filter
    category_filter = request.POST.get("category_filter", "")
    if category_filter in {value for value, _label in Feedback.Category.choices}:
        redirect_params["category"] = category_filter
    rating_filter = request.POST.get("rating_filter", "")
    if rating_filter in {str(value) for value in range(1, 6)}:
        redirect_params["rating"] = rating_filter
    query = request.POST.get("q", "").strip()[:200]
    if query:
        redirect_params["q"] = query
    destination = reverse("feedback_inbox")
    if redirect_params:
        destination += f"?{urlencode(redirect_params)}"
    return redirect(destination)


@login_required
def getting_started(request):
    context = workspace_context(request)
    if not context["membership"]:
        raise PermissionDenied
    active_shops = list(
        context["membership"].workspace.shops.filter(is_active=True).order_by("name")
    )
    has_orders = Order.objects.filter(shop__in=active_shops).exists()
    order_skus = {
        sku.strip().upper()
        for sku in OrderItem.objects.filter(order__shop__in=active_shops)
        .exclude(sku="")
        .values_list("sku", flat=True)
    }
    cost_skus = {
        sku.strip().upper()
        for sku in ProductCost.objects.filter(shop__in=active_shops).values_list("sku", flat=True)
    }
    missing_cost_skus = order_skus - cost_skus
    has_complete_costs = bool(order_skus) and not missing_cost_skus
    has_etsy_connection = EtsyConnection.objects.filter(
        shop__in=active_shops,
        is_active=True,
    ).exists()
    steps = [
        {
            "label": "Shop ready",
            "note": "Add the Etsy shop you want FeeLoom to track.",
            "done": bool(active_shops),
            "url": reverse("shops"),
            "action": "Manage shops",
        },
        {
            "label": "Sales data ready",
            "note": "Connect Etsy or import an Etsy sales CSV.",
            "done": has_orders,
            "url": reverse("import_sales"),
            "action": "Import sales",
        },
        {
            "label": "Product costs complete",
            "note": (
                f"Add costs for {len(missing_cost_skus)} sold SKU{'s' if len(missing_cost_skus) != 1 else ''}."
                if missing_cost_skus
                else "Every sold SKU has a product cost."
                if order_skus
                else "Import sales first to identify sold SKUs."
            ),
            "done": has_complete_costs,
            "url": reverse("product_costs"),
            "action": "Add costs",
        },
        {
            "label": "Profit overview ready",
            "note": "Review real margins after sales and product costs are available.",
            "done": has_orders and has_complete_costs,
            "url": reverse("dashboard"),
            "action": "View overview",
        },
    ]
    completed_steps = sum(step["done"] for step in steps)
    context.update(
        {
            "setup_steps": steps,
            "completed_steps": completed_steps,
            "total_steps": len(steps),
            "setup_complete": all(step["done"] for step in steps),
            "setup_percent": completed_steps / len(steps) * 100,
            "next_step": next((step for step in steps if not step["done"]), None),
            "has_etsy_connection": has_etsy_connection,
            "missing_cost_count": len(missing_cost_skus),
        }
    )
    return render(request, "dashboard/getting_started.html", context)


@login_required
def download_sample_csv(request):
    sample_path = Path(settings.BASE_DIR) / "sample_data" / "EtsySoldOrdersSample.csv"
    return FileResponse(
        sample_path.open("rb"),
        as_attachment=True,
        filename="FeeLoom-Etsy-Orders-Sample.csv",
        content_type="text/csv",
    )
