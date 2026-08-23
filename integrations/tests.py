from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sales.models import Order, ProductCost
from workspaces.models import Membership, Shop, Workspace

from .crypto import decrypt_token, encrypt_token
from .models import EtsyConnection, EtsySyncRun
from .etsy import EtsyAPIError, get_owner_shop, get_receipts
from .sync import SyncAlreadyRunning, sync_connection
from .views import OAUTH_SESSION_KEY


class EtsyIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="etsy-owner", password="test-pass-123")
        self.workspace = Workspace.objects.create(
            name="Etsy Studio", slug="etsy-studio", owner=self.user
        )
        Membership.objects.create(
            workspace=self.workspace, user=self.user, role=Membership.Role.OWNER
        )
        self.shop = Shop.objects.create(workspace=self.workspace, name="Etsy Shop")
        self.client.force_login(self.user)

    def test_tokens_are_encrypted_at_rest(self):
        encrypted = encrypt_token("private-token")

        self.assertNotIn("private-token", encrypted)
        self.assertEqual(decrypt_token(encrypted), "private-token")

    def test_connect_requires_server_credentials(self):
        response = self.client.get(reverse("etsy_connect", args=[self.shop.id]))

        self.assertRedirects(response, reverse("shops"))
        self.assertFalse(OAUTH_SESSION_KEY in self.client.session)

    @override_settings(
        ETSY_API_KEY="keystring",
        ETSY_SHARED_SECRET="shared-secret",
        ETSY_REDIRECT_URI="https://example.com/integrations/etsy/callback/",
    )
    def test_connect_starts_pkce_authorization(self):
        response = self.client.get(reverse("etsy_connect", args=[self.shop.id]))
        query = parse_qs(urlparse(response["Location"]).query)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(query["scope"], ["shops_r transactions_r"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(self.client.session[OAUTH_SESSION_KEY]["shop_id"], self.shop.id)

    @override_settings(ETSY_API_KEY="keystring", ETSY_SHARED_SECRET="shared-secret")
    @patch("integrations.views.get_owner_shop")
    @patch("integrations.views.exchange_code")
    def test_callback_saves_encrypted_connection(self, exchange_code_mock, owner_shop_mock):
        exchange_code_mock.return_value = {
            "access_token": "12345.access-secret",
            "refresh_token": "12345.refresh-secret",
            "expires_in": 3600,
            "scope": "shops_r transactions_r",
        }
        owner_shop_mock.return_value = {
            "shop_id": 98765,
            "shop_name": "Live Etsy Shop",
            "currency_code": "USD",
        }
        session = self.client.session
        session[OAUTH_SESSION_KEY] = {
            "state": "secure-state",
            "verifier": "verifier",
            "shop_id": self.shop.id,
            "redirect_uri": "https://example.com/callback/",
        }
        session.save()

        response = self.client.get(
            reverse("etsy_callback"), {"state": "secure-state", "code": "auth-code"}
        )

        connection = EtsyConnection.objects.get(shop=self.shop)
        self.shop.refresh_from_db()
        self.assertRedirects(response, reverse("shops"))
        self.assertEqual(self.shop.external_shop_id, "98765")
        self.assertNotIn("access-secret", connection.access_token_ciphertext)
        self.assertEqual(decrypt_token(connection.access_token_ciphertext), "12345.access-secret")

    def test_callback_rejects_wrong_state(self):
        session = self.client.session
        session[OAUTH_SESSION_KEY] = {
            "state": "expected",
            "verifier": "verifier",
            "shop_id": self.shop.id,
            "redirect_uri": "https://example.com/callback/",
        }
        session.save()

        response = self.client.get(
            reverse("etsy_callback"), {"state": "different", "code": "auth-code"}
        )

        self.assertRedirects(response, reverse("shops"))
        self.assertFalse(EtsyConnection.objects.exists())

    @patch("integrations.sync.get_receipt_payments")
    @patch("integrations.sync.get_receipts")
    def test_sync_imports_receipt_with_profit(self, receipts_mock, payments_mock):
        self.shop.external_shop_id = "98765"
        self.shop.save(update_fields=["external_shop_id"])
        ProductCost.objects.create(
            shop=self.shop,
            sku="RING-1",
            title="Gold Ring",
            materials=Decimal("6.00"),
            labor=Decimal("4.00"),
        )
        connection = EtsyConnection.objects.create(
            shop=self.shop,
            etsy_user_id="12345",
            access_token_ciphertext=encrypt_token("12345.access-secret"),
            refresh_token_ciphertext=encrypt_token("12345.refresh-secret"),
            token_expires_at=timezone.now() + timedelta(hours=1),
            scopes="shops_r transactions_r",
        )
        receipts_mock.return_value = [
            {
                "receipt_id": 555,
                "create_timestamp": int(timezone.now().timestamp()),
                "subtotal": {"amount": 5000, "divisor": 100, "currency_code": "USD"},
                "total_shipping_cost": {"amount": 0, "divisor": 100, "currency_code": "USD"},
                "discount_amt": {"amount": 0, "divisor": 100, "currency_code": "USD"},
                "transactions": [
                    {
                        "transaction_id": 1,
                        "title": "Gold Ring",
                        "sku": "RING-1",
                        "quantity": 1,
                        "price": {"amount": 5000, "divisor": 100, "currency_code": "USD"},
                    }
                ],
            }
        ]
        payments_mock.return_value = [
            {"amount_fees": {"amount": 500, "divisor": 100, "currency_code": "USD"}}
        ]

        run = sync_connection(connection)

        order = Order.objects.get(shop=self.shop, external_order_id="ETSY-555")
        self.assertEqual(run.status, EtsySyncRun.Status.SUCCESS)
        self.assertEqual(run.imported_orders, 1)
        self.assertEqual(order.marketplace_fees, Decimal("5"))
        self.assertEqual(order.product_cost, Decimal("10"))
        self.assertEqual(order.net_profit, Decimal("35"))

    @patch("integrations.etsy.request_json")
    def test_receipts_are_loaded_across_all_pages(self, request_json_mock):
        request_json_mock.side_effect = [
            {"count": 3, "results": [{"receipt_id": 1}, {"receipt_id": 2}]},
            {"count": 3, "results": [{"receipt_id": 3}]},
        ]

        receipts = get_receipts("98765", "token", page_size=2)

        self.assertEqual([receipt["receipt_id"] for receipt in receipts], [1, 2, 3])
        self.assertIn("offset=0", request_json_mock.call_args_list[0].args[0])
        self.assertIn("offset=2", request_json_mock.call_args_list[1].args[0])

    @patch("integrations.etsy.request_json")
    def test_missing_owner_shop_has_actionable_error(self, request_json_mock):
        request_json_mock.side_effect = EtsyAPIError(
            "Could not find a shop for user with user_id = 12345"
        )

        with self.assertRaisesRegex(EtsyAPIError, "does not own an active shop"):
            get_owner_shop("12345", "token")

    def test_overlapping_sync_is_rejected(self):
        connection = EtsyConnection.objects.create(
            shop=self.shop,
            etsy_user_id="12345",
            access_token_ciphertext=encrypt_token("12345.access-secret"),
            refresh_token_ciphertext=encrypt_token("12345.refresh-secret"),
            token_expires_at=timezone.now() + timedelta(hours=1),
            scopes="shops_r transactions_r",
        )
        EtsySyncRun.objects.create(connection=connection)

        with self.assertRaises(SyncAlreadyRunning):
            sync_connection(connection)

        self.assertEqual(connection.sync_runs.count(), 1)
