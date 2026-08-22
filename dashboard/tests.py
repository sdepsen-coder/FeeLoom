import sqlite3
import tempfile
import os
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.management.base import CommandError
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import BetaInvite, LegalAcceptance
from integrations.models import EtsyConnection
from sales.models import ImportBatch, Order, OrderItem, ProductCost
from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY, ALL_SHOPS_VALUE

from .models import AuditEvent, Feedback
from .management.commands.backup_database import Command as BackupDatabaseCommand


class BootstrapAdminCommandTests(TestCase):
    def test_command_is_inert_by_default(self):
        output = StringIO()

        call_command("bootstrap_admin", stdout=output)

        self.assertFalse(User.objects.filter(is_superuser=True).exists())
        self.assertIn("disabled", output.getvalue())

    @patch.dict(
        os.environ,
        {
            "FEELOOM_BOOTSTRAP_ADMIN_ON_START": "true",
            "FEELOOM_BOOTSTRAP_ADMIN_USERNAME": "launch-admin",
            "FEELOOM_BOOTSTRAP_ADMIN_EMAIL": "admin@example.com",
            "FEELOOM_BOOTSTRAP_ADMIN_PASSWORD": "Launch-pass-482!",
        },
        clear=False,
    )
    def test_command_creates_superuser_without_printing_password(self):
        output = StringIO()

        call_command("bootstrap_admin", stdout=output)

        user = User.objects.get(username="launch-admin")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("Launch-pass-482!"))
        self.assertNotIn("Launch-pass-482!", output.getvalue())

    @patch.dict(
        os.environ,
        {
            "FEELOOM_BOOTSTRAP_ADMIN_ON_START": "true",
            "FEELOOM_BOOTSTRAP_ADMIN_USERNAME": "seller",
            "FEELOOM_BOOTSTRAP_ADMIN_EMAIL": "seller@example.com",
            "FEELOOM_BOOTSTRAP_ADMIN_PASSWORD": "Launch-pass-593!",
        },
        clear=False,
    )
    def test_command_promotes_existing_user(self):
        User.objects.create_user(username="seller", password="old-password-123")

        call_command("bootstrap_admin")

        user = User.objects.get(username="seller")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("Launch-pass-593!"))


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="test-pass-123")
        self.workspace = Workspace.objects.create(
            name="Demo Studio",
            slug="demo-studio",
            owner=self.user,
        )
        Membership.objects.create(
            workspace=self.workspace,
            user=self.user,
            role=Membership.Role.OWNER,
        )
        self.shop = Shop.objects.create(workspace=self.workspace, name="Demo Shop")
        self.order = Order.objects.create(
            shop=self.shop,
            external_order_id="ORDER-100",
            ordered_at=timezone.now(),
            item_revenue=Decimal("40.00"),
            marketplace_fees=Decimal("4.00"),
            shipping_cost=Decimal("5.00"),
            product_cost=Decimal("11.00"),
            net_profit=Decimal("20.00"),
            margin_percent=Decimal("50.00"),
            profit_status=Order.ProfitStatus.PROFITABLE,
        )
        OrderItem.objects.create(
            order=self.order,
            sku="MUG-100",
            title="Ceramic Mug",
            quantity=1,
            unit_price=Decimal("40.00"),
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_authenticated_user_without_workspace_gets_safe_empty_state(self):
        orphan = User.objects.create_user(username="orphan-seller")
        self.client.force_login(orphan)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No active workspace")
        self.assertNotContains(response, "Sales")

    def test_superuser_without_workspace_gets_operations_link(self):
        admin = User.objects.create_superuser(
            username="operations-admin",
            email="admin@example.com",
            password="strong-admin-password-123",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operations account")
        self.assertContains(response, reverse("system_status"))
        self.assertNotContains(response, "Contact admin@example.com")

    def test_health_check_is_public_and_checks_database(self):
        response = self.client.get(reverse("health_check"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertRegex(
            response["X-Request-ID"],
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )

    def test_dashboard_and_sales_show_current_shop_data(self):
        self.client.force_login(self.user)

        dashboard_response = self.client.get(reverse("dashboard"))
        sales_response = self.client.get(reverse("sales_table"))

        self.assertContains(dashboard_response, "USD 40.00")
        self.assertContains(sales_response, "ORDER-100")
        self.assertContains(sales_response, "Ceramic Mug")

    def test_authenticated_layout_has_accessible_mobile_navigation(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, 'aria-controls="workspace-nav"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'id="workspace-nav"')
        self.assertContains(response, "Sign out", count=2)

    def test_sales_table_has_mobile_profit_labels(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("sales_table"))

        self.assertContains(response, 'class="order-table"')
        self.assertContains(response, 'data-label="Net profit"')
        self.assertContains(response, 'data-label="Margin"')

    def test_other_workspace_order_is_hidden(self):
        other_user = User.objects.create_user(username="other")
        other_workspace = Workspace.objects.create(
            name="Other Studio",
            slug="other-studio",
            owner=other_user,
        )
        other_shop = Shop.objects.create(workspace=other_workspace, name="Other Shop")
        hidden = Order.objects.create(
            shop=other_shop,
            external_order_id="HIDDEN-1",
            ordered_at=timezone.now(),
        )
        self.client.force_login(self.user)

        list_response = self.client.get(reverse("sales_table"))
        detail_response = self.client.get(reverse("sale_detail", args=[hidden.id]))

        self.assertNotContains(list_response, "HIDDEN-1")
        self.assertEqual(detail_response.status_code, 404)

    def test_product_cost_is_saved_to_current_shop(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("product_costs"),
            {
                "sku": "mug-100",
                "title": "Ceramic Mug",
                "materials": "3.00",
                "packaging": "1.00",
                "labor": "4.00",
                "overhead": "2.00",
            },
        )

        cost = ProductCost.objects.get(shop=self.shop, sku="MUG-100")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(cost.unit_cost, Decimal("10.00"))

    def test_csv_export_contains_filtered_shop_orders(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("export_sales"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("ORDER-100", response.content.decode())

    def test_sales_are_paginated_and_filters_survive_page_navigation(self):
        for index in range(30):
            Order.objects.create(
                shop=self.shop,
                external_order_id=f"BATCH-{index:02d}",
                ordered_at=timezone.now() - timedelta(minutes=index + 1),
                profit_status=Order.ProfitStatus.PROFITABLE,
            )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("sales_table"),
            {"q": "BATCH", "status": Order.ProfitStatus.PROFITABLE, "page": 2},
        )

        self.assertEqual(response.context["sales_page"].number, 2)
        self.assertEqual(response.context["sales_page"].paginator.count, 30)
        self.assertEqual(
            response.context["filter_query"],
            "q=BATCH&status=profitable",
        )
        self.assertContains(response, "BATCH-29")
        self.assertNotContains(response, "BATCH-00")

    def test_csv_export_escapes_spreadsheet_formulas(self):
        self.order.external_order_id = "=DANGEROUS()"
        self.order.save(update_fields=["external_order_id"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("export_sales"))

        self.assertIn("'=DANGEROUS()", response.content.decode())

    def test_orders_csv_can_be_imported_from_dashboard(self):
        self.client.force_login(self.user)
        uploaded = SimpleUploadedFile(
            "EtsySoldOrders2026.csv",
            (
                "Sale Date,Order ID,Number of Items,Currency,Order Value,"
                "Discount Amount,Shipping,Card Processing Fees,SKU\n"
                "08/16/2026,ETSY-300,1,USD,30.00,0,0,1.15,NEW-1\n"
            ).encode(),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("import_sales"), {"shop": self.shop.id, "csv_file": uploaded}
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Order.objects.filter(shop=self.shop, external_order_id="ETSY-300").exists()
        )

    def test_import_result_cannot_show_other_shop_batch(self):
        other_user = User.objects.create_user(username="batch-owner")
        other_workspace = Workspace.objects.create(
            name="Batch Studio",
            slug="batch-studio",
            owner=other_user,
        )
        other_shop = Shop.objects.create(workspace=other_workspace, name="Batch Shop")
        other_batch = ImportBatch.objects.create(shop=other_shop, file_name="private.csv")
        self.client.force_login(self.user)

        response = self.client.get(reverse("import_sales"), {"batch": other_batch.id})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "private.csv")

    def test_all_shops_combines_orders_and_identifies_shop(self):
        second_shop = Shop.objects.create(workspace=self.workspace, name="Second Shop")
        Order.objects.create(
            shop=second_shop,
            external_order_id="ORDER-200",
            ordered_at=timezone.now(),
            item_revenue=Decimal("60.00"),
            net_profit=Decimal("30.00"),
            profit_status=Order.ProfitStatus.PROFITABLE,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session[ACTIVE_SHOP_SESSION_KEY] = ALL_SHOPS_VALUE
        session.save()

        dashboard_response = self.client.get(reverse("dashboard"))
        sales_response = self.client.get(reverse("sales_table"))

        self.assertContains(dashboard_response, "USD 100.00")
        self.assertContains(sales_response, "ORDER-100")
        self.assertContains(sales_response, "ORDER-200")
        self.assertContains(sales_response, "Second Shop")

    def test_switching_shop_scopes_sales(self):
        second_shop = Shop.objects.create(workspace=self.workspace, name="Second Shop")
        Order.objects.create(
            shop=second_shop,
            external_order_id="ORDER-200",
            ordered_at=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("switch_shop"), {"shop": second_shop.id, "next": reverse("sales_table")}
        )
        sales_response = self.client.get(reverse("sales_table"))

        self.assertRedirects(response, reverse("sales_table"))
        self.assertEqual(self.client.session[ACTIVE_SHOP_SESSION_KEY], second_shop.id)
        self.assertContains(sales_response, "ORDER-200")
        self.assertNotContains(sales_response, "ORDER-100")

    def test_cannot_switch_to_another_workspace_shop(self):
        other_user = User.objects.create_user(username="outsider")
        other_workspace = Workspace.objects.create(
            name="Outside", slug="outside", owner=other_user
        )
        outside_shop = Shop.objects.create(workspace=other_workspace, name="Private Shop")
        self.client.force_login(self.user)

        response = self.client.post(reverse("switch_shop"), {"shop": outside_shop.id})

        self.assertEqual(response.status_code, 404)

    def test_owner_can_add_a_second_shop(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("shops"), {"name": "New Store", "currency": "usd"}
        )

        new_shop = Shop.objects.get(workspace=self.workspace, name="New Store")
        self.assertRedirects(response, reverse("shops"))
        self.assertEqual(new_shop.currency, "USD")
        self.assertEqual(self.client.session[ACTIVE_SHOP_SESSION_KEY], new_shop.id)

    def test_import_rejects_shop_from_another_workspace(self):
        other_user = User.objects.create_user(username="import-outsider")
        other_workspace = Workspace.objects.create(
            name="Import Outside", slug="import-outside", owner=other_user
        )
        outside_shop = Shop.objects.create(workspace=other_workspace, name="Outside Import")
        self.client.force_login(self.user)
        uploaded = SimpleUploadedFile(
            "orders.csv",
            b"Sale Date,Order ID,Order Value\n08/16/2026,PRIVATE-1,30.00\n",
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("import_sales"), {"shop": outside_shop.id, "csv_file": uploaded}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(external_order_id="PRIVATE-1").exists())

    @override_settings(
        EMAIL_DELIVERY_ENABLED=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="FeeLoom <account@example.com>",
        FEELOOM_SUPPORT_EMAIL="support@example.com",
    )
    def test_feedback_is_saved_and_support_is_notified(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("feedback"),
            {
                "category": Feedback.Category.CALCULATION,
                "rating": 4,
                "message": "The fee total needs a clearer breakdown.",
                "page_path": "/sales/1/",
            },
        )

        submission = Feedback.objects.get()
        self.assertRedirects(response, f"{reverse('feedback')}?sent=1")
        self.assertEqual(submission.workspace, self.workspace)
        self.assertEqual(submission.shop, self.shop)
        self.assertEqual(submission.user, self.user)
        self.assertEqual(submission.page_path, "/sales/1/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["support@example.com"])
        self.assertIn("Calculation", mail.outbox[0].subject)
        self.assertIn("The fee total needs a clearer breakdown.", mail.outbox[0].body)

    @override_settings(
        EMAIL_DELIVERY_ENABLED=True,
        FEELOOM_SUPPORT_EMAIL="support@example.com",
    )
    @patch("dashboard.views.send_mail", side_effect=RuntimeError("provider unavailable"))
    def test_feedback_is_saved_when_notification_fails(self, send_mail_mock):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("feedback"),
            {
                "category": Feedback.Category.BUG,
                "rating": 2,
                "message": "The filter stopped responding.",
                "page_path": "/sales/",
            },
        )

        self.assertRedirects(response, f"{reverse('feedback')}?sent=1")
        self.assertTrue(Feedback.objects.filter(message="The filter stopped responding.").exists())
        send_mail_mock.assert_called_once()

    def test_feedback_rating_scale_is_explained(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("feedback"))

        self.assertContains(response, "1 = worst, 5 = best")
        self.assertContains(response, "1 - Worst")
        self.assertContains(response, "5 - Best")

    def test_feedback_rejects_external_source_url(self):
        self.client.force_login(self.user)

        self.client.post(
            reverse("feedback"),
            {
                "category": Feedback.Category.BUG,
                "rating": 2,
                "message": "Something did not work.",
                "page_path": "https://malicious.example/",
            },
        )

        self.assertEqual(Feedback.objects.get().page_path, "")

    def test_getting_started_shows_workspace_progress(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("getting_started"))

        self.assertContains(response, "2 of 3 complete")
        self.assertContains(response, "Shop created")
        self.assertContains(response, "Orders imported")

    def test_sample_etsy_csv_can_be_downloaded(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("download_sample_csv"))
        content = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(b"Order ID", content)

    def test_workspace_export_contains_only_current_workspace_and_no_tokens(self):
        LegalAcceptance.objects.create(user=self.user, version="2026-08-21")
        EtsyConnection.objects.create(
            shop=self.shop,
            etsy_user_id="etsy-user-1",
            access_token_ciphertext="secret-access-token",
            refresh_token_ciphertext="secret-refresh-token",
            token_expires_at=timezone.now(),
        )
        other_user = User.objects.create_user(username="export-outsider")
        other_workspace = Workspace.objects.create(
            name="Outside Export", slug="outside-export", owner=other_user
        )
        other_shop = Shop.objects.create(workspace=other_workspace, name="Hidden Export Shop")
        Order.objects.create(
            shop=other_shop,
            external_order_id="PRIVATE-EXPORT-1",
            ordered_at=timezone.now(),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("export_workspace_data"))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("ORDER-100", content)
        self.assertIn("2026-08-21", content)
        self.assertNotIn("PRIVATE-EXPORT-1", content)
        self.assertNotIn("access_token_ciphertext", content)
        self.assertNotIn("refresh_token_ciphertext", content)
        self.assertNotIn("secret-access-token", content)
        self.assertNotIn("secret-refresh-token", content)

    def test_only_workspace_owner_can_delete_workspace(self):
        manager = User.objects.create_user(username="manager", password="manager-pass")
        Membership.objects.create(
            workspace=self.workspace,
            user=manager,
            role=Membership.Role.MANAGER,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("delete_workspace"),
            {"workspace_name": self.workspace.name, "password": "manager-pass"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Workspace.objects.filter(id=self.workspace.id).exists())

        export_response = self.client.get(reverse("export_workspace_data"))
        self.assertEqual(export_response.status_code, 403)

    def test_workspace_deletion_requires_exact_name_and_password(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("delete_workspace"),
            {"workspace_name": "Wrong name", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Workspace.objects.filter(id=self.workspace.id).exists())
        self.assertContains(response, "Enter the workspace name exactly as shown.", status_code=400)
        self.assertContains(response, "Your password is incorrect.", status_code=400)

    def test_workspace_owner_can_delete_only_workspace_and_account(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("delete_workspace"),
            {"workspace_name": self.workspace.name, "password": "test-pass-123"},
        )

        self.assertRedirects(response, reverse("login"))
        self.assertFalse(Workspace.objects.filter(id=self.workspace.id).exists())
        self.assertFalse(User.objects.filter(id=self.user.id).exists())
        self.assertFalse(Order.objects.filter(id=self.order.id).exists())

    def test_workspace_deletion_keeps_account_with_another_active_membership(self):
        other_owner = User.objects.create_user(username="other-owner")
        other_workspace = Workspace.objects.create(
            name="Shared Studio", slug="shared-studio", owner=other_owner
        )
        Membership.objects.create(
            workspace=other_workspace,
            user=self.user,
            role=Membership.Role.MANAGER,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("delete_workspace"),
            {"workspace_name": self.workspace.name, "password": "test-pass-123"},
        )

        self.assertRedirects(response, reverse("login"))
        self.assertTrue(User.objects.filter(id=self.user.id).exists())
        self.assertTrue(
            Membership.objects.filter(user=self.user, workspace=other_workspace).exists()
        )

    def test_staff_owner_can_create_and_disable_beta_invite(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("beta_invites"),
            {"label": "First tester", "max_uses": 2, "valid_for_days": 14},
        )

        invite = BetaInvite.objects.get(workspace=self.workspace)
        self.assertRedirects(response, f"{reverse('beta_invites')}?created={invite.id}")
        self.assertEqual(invite.max_uses, 2)
        self.assertTrue(invite.is_available)
        created_event = AuditEvent.objects.get(action="beta_invite.created")
        self.assertEqual(created_event.workspace, self.workspace)
        self.assertEqual(created_event.user, self.user)
        self.assertTrue(created_event.request_id)

        toggle_response = self.client.post(reverse("toggle_invite", args=[invite.id]))
        invite.refresh_from_db()
        self.assertRedirects(toggle_response, reverse("beta_invites"))
        self.assertFalse(invite.is_active)
        self.assertTrue(
            AuditEvent.objects.filter(
                workspace=self.workspace,
                action="beta_invite.disabled",
            ).exists()
        )

    def test_non_staff_owner_cannot_manage_beta_invites(self):
        self.client.force_login(self.user)

        list_response = self.client.get(reverse("beta_invites"))
        create_response = self.client.post(
            reverse("beta_invites"),
            {"label": "Unauthorized owner", "max_uses": 1, "valid_for_days": 7},
        )
        dashboard_response = self.client.get(reverse("dashboard"))

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertNotContains(dashboard_response, "Beta invites")
        self.assertFalse(BetaInvite.objects.filter(label="Unauthorized owner").exists())

    def test_manager_cannot_manage_beta_invites(self):
        manager = User.objects.create_user(username="invite-manager")
        Membership.objects.create(
            workspace=self.workspace,
            user=manager,
            role=Membership.Role.MANAGER,
        )
        self.client.force_login(manager)

        list_response = self.client.get(reverse("beta_invites"))
        create_response = self.client.post(
            reverse("beta_invites"),
            {"label": "Unauthorized", "max_uses": 1, "valid_for_days": 7},
        )
        activity_response = self.client.get(reverse("activity"))

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(activity_response.status_code, 403)
        self.assertFalse(BetaInvite.objects.filter(label="Unauthorized").exists())

    def test_activity_is_owner_only_and_scoped_to_workspace(self):
        AuditEvent.objects.create(
            workspace=self.workspace,
            user=self.user,
            action="shop.created",
            summary="Visible activity",
            request_id="visible-request",
        )
        other_user = User.objects.create_user(username="activity-outsider")
        other_workspace = Workspace.objects.create(
            name="Activity Outside", slug="activity-outside", owner=other_user
        )
        AuditEvent.objects.create(
            workspace=other_workspace,
            user=other_user,
            action="shop.created",
            summary="Private activity",
            request_id="private-request",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("activity"))

        self.assertContains(response, "Visible activity")
        self.assertContains(response, "visible-request")
        self.assertNotContains(response, "Private activity")
        self.assertNotContains(response, "private-request")

    def test_owner_cannot_toggle_another_workspace_invite(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        other_user = User.objects.create_user(username="invite-outsider")
        other_workspace = Workspace.objects.create(
            name="Invite Outside", slug="invite-outside", owner=other_user
        )
        outside_invite = BetaInvite.objects.create(
            workspace=other_workspace,
            created_by=other_user,
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse("toggle_invite", args=[outside_invite.id]))

        self.assertEqual(response.status_code, 404)
        outside_invite.refresh_from_db()
        self.assertTrue(outside_invite.is_active)


@skipUnless(connection.vendor == "sqlite", "SQLite backup verification")
class DatabaseBackupCommandTests(TransactionTestCase):
    def test_backup_database_creates_readable_copy(self):
        User.objects.create_user(username="backup-owner")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "feeloom.sqlite3"

            call_command("backup_database", output=str(output))

            self.assertTrue(output.exists())
            backup = sqlite3.connect(output)
            try:
                username = backup.execute(
                    "SELECT username FROM auth_user WHERE username = ?",
                    ("backup-owner",),
                ).fetchone()
            finally:
                backup.close()
            self.assertEqual(username, ("backup-owner",))

    def test_backup_database_refuses_to_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.sqlite3"
            output.write_text("keep", encoding="utf-8")

            with self.assertRaisesMessage(CommandError, "Backup already exists"):
                call_command("backup_database", output=str(output))

            self.assertEqual(output.read_text(encoding="utf-8"), "keep")


class PostgreSQLBackupCommandTests(SimpleTestCase):
    @patch("dashboard.management.commands.backup_database.subprocess.run")
    @patch(
        "dashboard.management.commands.backup_database.shutil.which",
        return_value="C:/PostgreSQL/bin/pg_dump.exe",
    )
    def test_postgresql_backup_uses_custom_format_without_exposing_password(
        self, _which, run
    ):
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        database = SimpleNamespace(
            settings_dict={
                "NAME": "feeloom",
                "HOST": "database.example",
                "PORT": "5432",
                "USER": "feeloom_user",
                "PASSWORD": "private-password",
                "OPTIONS": {"sslmode": "require"},
            }
        )

        BackupDatabaseCommand._backup_postgresql(database, Path("backup.dump"))

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertIn("--format=custom", command)
        self.assertIn("--no-owner", command)
        self.assertIn("--host=database.example", command)
        self.assertNotIn("private-password", command)
        self.assertEqual(environment["PGPASSWORD"], "private-password")
        self.assertEqual(environment["PGSSLMODE"], "require")


class LaunchCheckCommandTests(TransactionTestCase):
    @override_settings(DEBUG=True)
    def test_production_check_reports_local_blockers(self):
        output = StringIO()

        call_command("launch_check", production=True, no_fail=True, stdout=output)

        report = output.getvalue()
        self.assertIn("BLOCK Database engine", report)
        self.assertIn("BLOCK Debug mode", report)
        self.assertIn("Summary:", report)

    @override_settings(
        DEBUG=False,
        SECRET_KEY="a-strong-production-secret-with-many-unique-characters-1234567890",
        FEELOOM_TOKEN_ENCRYPTION_KEY="private-encryption-key-value",
        FEELOOM_SUPPORT_EMAIL="support@example.com",
        EMAIL_DELIVERY_ENABLED=True,
        EMAIL_HOST="smtp.example.com",
        EMAIL_HOST_USER="smtp-user",
        EMAIL_HOST_PASSWORD="private-smtp-password",
        CONFIGURED_FROM_EMAIL="support@example.com",
        ETSY_API_KEY="private-etsy-key",
        ETSY_SHARED_SECRET="private-etsy-secret",
        ETSY_REDIRECT_URI="https://example.com/integrations/etsy/callback/",
        SENTRY_DSN="https://private-sentry-dsn@example.com/1",
    )
    def test_check_never_prints_secret_values(self):
        output = StringIO()

        call_command("launch_check", production=True, no_fail=True, stdout=output)

        report = output.getvalue()
        self.assertNotIn("private-encryption-key-value", report)
        self.assertNotIn("private-etsy-key", report)
        self.assertNotIn("private-etsy-secret", report)
        self.assertNotIn("private-sentry-dsn", report)
        self.assertIn("PASS  Etsy credentials", report)


class SystemStatusTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="system-admin",
            email="admin@example.com",
            password="admin-pass-123",
        )
        self.workspace = Workspace.objects.create(
            name="System Studio",
            slug="system-studio",
            owner=self.admin,
        )
        Membership.objects.create(
            workspace=self.workspace,
            user=self.admin,
            role=Membership.Role.OWNER,
        )
        self.shop = Shop.objects.create(workspace=self.workspace, name="System Shop")

    def test_system_status_is_superuser_only(self):
        regular = User.objects.create_user(username="regular", password="regular-pass")
        Membership.objects.create(
            workspace=self.workspace,
            user=regular,
            role=Membership.Role.MANAGER,
        )
        self.client.force_login(regular)

        response = self.client.get(reverse("system_status"))

        self.assertEqual(response.status_code, 403)

    def test_superuser_can_view_readiness_without_secret_values(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("system_status"))

        self.assertContains(response, "Beta readiness")
        self.assertContains(response, "Database")
        self.assertNotContains(response, settings.SECRET_KEY)

    def test_feedback_inbox_is_superuser_only(self):
        staff_user = User.objects.create_user(
            username="support-staff", password="staff-pass", is_staff=True
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("feedback_inbox"))

        self.assertEqual(response.status_code, 403)

    def test_superuser_can_review_feedback(self):
        submission = Feedback.objects.create(
            workspace=self.workspace,
            shop=self.shop,
            user=self.admin,
            category=Feedback.Category.USABILITY,
            rating=3,
            message="The import step needs a clearer label.",
            page_path="/sales/import/",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("feedback_inbox"))

        self.assertContains(response, "The import step needs a clearer label.")
        self.assertContains(response, "System Studio")

        update_response = self.client.post(
            reverse("update_feedback_status", args=[submission.id]),
            {"status": Feedback.Status.REVIEWING},
        )
        submission.refresh_from_db()
        self.assertRedirects(update_response, reverse("feedback_inbox"))
        self.assertEqual(submission.status, Feedback.Status.REVIEWING)

    @override_settings(
        EMAIL_DELIVERY_ENABLED=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="FeeLoom <account@example.com>",
        CONFIGURED_FROM_EMAIL="FeeLoom <account@example.com>",
        EMAIL_HOST="smtp.example.com",
        EMAIL_HOST_USER="smtp-user",
        EMAIL_HOST_PASSWORD="smtp-password",
    )
    def test_superuser_can_send_account_email_test(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("system_status"),
            {"recipient": "owner@example.com"},
        )

        self.assertRedirects(response, reverse("system_status"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@example.com"])
        self.assertTrue(
            AuditEvent.objects.filter(action="system.email_test_succeeded").exists()
        )
