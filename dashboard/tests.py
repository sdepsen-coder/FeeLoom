from decimal import Decimal
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BetaInvite, LegalAcceptance
from integrations.models import EtsyConnection
from sales.models import ImportBatch, Order, OrderItem, ProductCost
from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY, ALL_SHOPS_VALUE

from .models import AuditEvent, Feedback


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

    def test_feedback_is_saved_to_current_workspace_and_shop(self):
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

    def test_owner_can_create_and_disable_beta_invite(self):
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
