import re
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY
from .models import BetaInvite, LegalAcceptance


@override_settings(EMAIL_VERIFICATION_REQUIRED=False)
class SignupTests(TestCase):
    def setUp(self):
        cache.clear()
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

    @override_settings(
        EMAIL_DELIVERY_ENABLED=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_password_reset_sends_email_without_exposing_account_lookup(self):
        user = User.objects.create_user(
            username="reset-seller",
            email="reset@example.com",
            password="old-password-123",
        )

        response = self.client.post(
            reverse("password_reset"), {"email": "reset@example.com"}
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Reset your FeeLoom password", mail.outbox[0].subject)
        self.assertIn("/accounts/reset/", mail.outbox[0].body)
        self.assertIn(user.email, mail.outbox[0].to)

        reset_url = re.search(r"http://testserver(\S+)", mail.outbox[0].body).group(1)
        confirm_response = self.client.get(reset_url)
        self.assertEqual(confirm_response.status_code, 302)
        new_password_url = confirm_response["Location"]
        update_response = self.client.post(
            new_password_url,
            {
                "new_password1": "new-strong-password-456",
                "new_password2": "new-strong-password-456",
            },
        )
        self.assertRedirects(update_response, reverse("password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("new-strong-password-456"))

        unknown_response = self.client.post(
            reverse("password_reset"), {"email": "unknown@example.com"}
        )
        self.assertRedirects(unknown_response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_password_reset_is_hidden_and_unavailable_without_email_delivery(self):
        login_response = self.client.get(reverse("login"))
        reset_response = self.client.get(reverse("password_reset"))

        self.assertNotContains(login_response, "Forgot your password?")
        self.assertContains(reset_response, "Password reset is not available yet")

    @override_settings(
        EMAIL_DELIVERY_ENABLED=True,
        EMAIL_VERIFICATION_REQUIRED=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_signup_requires_single_use_email_verification(self):
        response = self.client.post(
            reverse("signup"),
            {
                "invite_code": self.invite.code,
                "username": "verify-seller",
                "email": "verify@example.com",
                "workspace_name": "Verify Studio",
                "shop_name": "Verify Shop",
                "password1": "strong-start-pass-123",
                "password2": "strong-start-pass-123",
                "accept_terms": "on",
            },
        )

        user = User.objects.get(username="verify-seller")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check your email")
        self.assertFalse(user.is_active)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 1)

        verification_url = re.search(
            r"http://testserver(\S+)", mail.outbox[0].body
        ).group(1)
        verification_response = self.client.get(verification_url)
        self.assertRedirects(verification_response, reverse("getting_started"))
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIn("_auth_user_id", self.client.session)

        reused_response = self.client.get(verification_url)
        self.assertEqual(reused_response.status_code, 400)
        self.assertContains(reused_response, "no longer valid", status_code=400)

    @override_settings(
        EMAIL_DELIVERY_ENABLED=True,
        EMAIL_VERIFICATION_REQUIRED=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_verification_email_can_be_resent_without_account_disclosure_or_spam(self):
        cache.clear()
        User.objects.create_user(
            username="inactive-seller",
            email="inactive@example.com",
            password="strong-password-123",
            is_active=False,
        )

        first_response = self.client.post(
            reverse("resend_verification"), {"email": "inactive@example.com"}
        )
        second_response = self.client.post(
            reverse("resend_verification"), {"email": "inactive@example.com"}
        )
        unknown_response = self.client.post(
            reverse("resend_verification"), {"email": "unknown@example.com"}
        )

        self.assertRedirects(first_response, reverse("verification_resent"))
        self.assertRedirects(second_response, reverse("verification_resent"))
        self.assertRedirects(unknown_response, reverse("verification_resent"))
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        LOGIN_IDENTITY_FAILURE_LIMIT=2,
        LOGIN_IP_FAILURE_LIMIT=10,
        PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    )
    def test_login_blocks_repeated_failures_for_same_identity(self):
        cache.clear()
        User.objects.create_user(
            username="protected-seller", password="correct-password"
        )
        for _ in range(2):
            response = self.client.post(
                reverse("login"),
                {"username": "protected-seller", "password": "wrong-password"},
            )
            self.assertEqual(response.status_code, 200)

        blocked_response = self.client.post(
            reverse("login"),
            {"username": "protected-seller", "password": "correct-password"},
        )

        self.assertEqual(blocked_response.status_code, 429)
        self.assertEqual(blocked_response["Retry-After"], "900")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_user_is_redirected_away_from_login(self):
        user = User.objects.create_user(username="signed-in-seller")
        self.client.force_login(user)

        response = self.client.get(reverse("login"))

        self.assertRedirects(response, reverse("dashboard"))

    @override_settings(SIGNUP_IP_LIMIT=2)
    def test_signup_rate_limit_blocks_automated_attempts(self):
        cache.clear()
        for _ in range(2):
            response = self.client.post(reverse("signup"), {})
            self.assertEqual(response.status_code, 200)

        blocked_response = self.client.post(reverse("signup"), {})

        self.assertEqual(blocked_response.status_code, 429)
        self.assertContains(blocked_response, "Please wait and try again", status_code=429)
