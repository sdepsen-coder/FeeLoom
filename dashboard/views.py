import csv
import json
import logging
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.core.serializers.json import DjangoJSONEncoder
from django.core.paginator import Paginator
from django.db.models import Q, Sum
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
from workspaces.models import Membership, Shop
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
    if not context["membership"]:
        context["support_email"] = settings.FEELOOM_SUPPORT_EMAIL
        return render(request, "dashboard/no_workspace.html", context)
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
            "show_getting_started": not orders.exists(),
        }
    )
    return render(request, "dashboard/home.html", context)


@login_required
def sales_table(request):
    context = workspace_context(request)
    orders, query, status = filtered_sales(request, context)
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
            "profit_statuses": Order.ProfitStatus.choices,
        }
    )
    return render(request, "dashboard/sales.html", context)


@login_required
def export_sales(request):
    context = workspace_context(request)
    orders, _, _ = filtered_sales(request, context)
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
    context.update(
        {
            "form": form,
            "can_manage_shops": can_manage_shops(context["membership"]),
            "workspace_shops": context["membership"].workspace.shops.select_related("etsy_connection").all() if context["membership"] else [],
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
        return redirect(f"{reverse('feedback')}?sent=1")
    context.update({"form": form, "feedback_sent": request.GET.get("sent") == "1"})
    return render(request, "dashboard/feedback.html", context)


@login_required
def privacy_data(request):
    context = workspace_context(request)
    if not context["membership"]:
        raise PermissionDenied
    owner = is_workspace_owner(request, context["membership"])
    context.update(
        {
            "can_delete_workspace": owner,
            "delete_form": DeleteWorkspaceForm(
                user=request.user,
                workspace=context["membership"].workspace,
            ) if owner else None,
        }
    )
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
        context.update({"can_delete_workspace": True, "delete_form": form})
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
    invites = membership.workspace.beta_invites.select_related("created_by")
    created_id = request.GET.get("created", "")
    context.update(
        {
            "form": form,
            "invites": invites,
            "created_invite": invites.filter(id=created_id).first() if created_id.isdigit() else None,
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
    events = AuditEvent.objects.filter(workspace=membership.workspace).select_related(
        "shop", "user"
    )
    context["activity_page"] = Paginator(events, 25).get_page(request.GET.get("page"))
    return render(request, "dashboard/activity.html", context)


@login_required
def system_status(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    context = workspace_context(request)
    form = SystemEmailTestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
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
        }
    )
    return render(request, "dashboard/system_status.html", context)


@login_required
def getting_started(request):
    context = workspace_context(request)
    if not context["membership"]:
        raise PermissionDenied
    active_shops = context["shops"]
    has_costs = ProductCost.objects.filter(shop__in=active_shops).exists()
    has_orders = Order.objects.filter(shop__in=active_shops).exists()
    steps = [
        {"label": "Shop created", "done": bool(active_shops), "url": reverse("shops")},
        {"label": "Product costs added", "done": has_costs, "url": reverse("product_costs")},
        {"label": "Orders imported", "done": has_orders, "url": reverse("import_sales")},
    ]
    context.update(
        {
            "setup_steps": steps,
            "completed_steps": sum(step["done"] for step in steps),
            "total_steps": len(steps),
            "setup_complete": all(step["done"] for step in steps),
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
