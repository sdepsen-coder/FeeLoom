from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY
from .models import BetaInvite, LegalAcceptance


class SignupTests(TestCase):
    def setUp(self):
        issuer = User.objects.create_user(username="beta-owner")
        issuer_workspace = Workspace.objects.create(
            name="Beta Studio", slug="beta-studio", owner=issuer
        )
        self.invite = BetaInvite.objects.create(
            workspace=issuer_workspace,
            created_by=issuer,
            expires_at=timezone.now() + timedelta(days=14),
        )

    def test_signup_creates_workspace_shop_and_owner_membership(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "maker",
                "invite_code": self.invite.code,
                "email": "maker@example.com",
                "workspace_name": "Maker Studio",
                "shop_name": "Studio Gifts",
                "password1": "strong-start-pass-123",
                "password2": "strong-start-pass-123",
                "accept_terms": "on",
            },
        )

        user = User.objects.get(username="maker")
        workspace = Workspace.objects.get(owner=user)
        membership = Membership.objects.get(workspace=workspace, user=user)
        shop = Shop.objects.get(workspace=workspace)
        self.assertRedirects(response, reverse("getting_started"))
        self.assertEqual(workspace.name, "Maker Studio")
        self.assertEqual(shop.name, "Studio Gifts")
        self.assertEqual(membership.role, Membership.Role.OWNER)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session[ACTIVE_SHOP_SESSION_KEY], shop.id)
        self.assertTrue(LegalAcceptance.objects.filter(user=user).exists())
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.use_count, 1)

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(username="existing", email="maker@example.com")

        response = self.client.post(
            reverse("signup"),
            {
                "username": "other",
                "invite_code": self.invite.code,
                "email": "MAKER@example.com",
                "workspace_name": "Other Studio",
                "shop_name": "Other Gifts",
                "password1": "strong-start-pass-123",
                "password2": "strong-start-pass-123",
                "accept_terms": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists")
        self.assertFalse(Workspace.objects.filter(name="Other Studio").exists())

    def test_signup_requires_terms_acceptance(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "no-terms",
                "invite_code": self.invite.code,
                "email": "no-terms@example.com",
                "workspace_name": "No Terms Studio",
                "shop_name": "No Terms Shop",
                "password1": "strong-start-pass-123",
                "password2": "strong-start-pass-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.assertFalse(User.objects.filter(username="no-terms").exists())

    def test_signup_rejects_expired_or_used_invite(self):
        self.invite.use_count = self.invite.max_uses
        self.invite.save(update_fields=["use_count"])

        response = self.client.post(
            reverse("signup"),
            {
                "invite_code": self.invite.code,
                "username": "late-seller",
                "email": "late@example.com",
                "workspace_name": "Late Studio",
                "shop_name": "Late Shop",
                "password1": "strong-start-pass-123",
                "password2": "strong-start-pass-123",
                "accept_terms": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid, expired, or fully used")
        self.assertFalse(User.objects.filter(username="late-seller").exists())

    def test_legal_pages_are_public(self):
        privacy_response = self.client.get(reverse("privacy_policy"))
        terms_response = self.client.get(reverse("terms_of_service"))

        self.assertEqual(privacy_response.status_code, 200)
        self.assertEqual(terms_response.status_code, 200)
        self.assertContains(privacy_response, "Privacy Policy")
        self.assertContains(terms_response, "Terms of Service")
