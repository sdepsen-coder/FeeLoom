from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from workspaces.models import Membership, Shop, Workspace
from workspaces.selectors import ACTIVE_SHOP_SESSION_KEY


class SignupTests(TestCase):
    def test_signup_creates_workspace_shop_and_owner_membership(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "maker",
                "email": "maker@example.com",
                "workspace_name": "Maker Studio",
                "shop_name": "Studio Gifts",
                "password1": "strong-start-pass-123",
                "password2": "strong-start-pass-123",
            },
        )

        user = User.objects.get(username="maker")
        workspace = Workspace.objects.get(owner=user)
        membership = Membership.objects.get(workspace=workspace, user=user)
        shop = Shop.objects.get(workspace=workspace)
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(workspace.name, "Maker Studio")
        self.assertEqual(shop.name, "Studio Gifts")
        self.assertEqual(membership.role, Membership.Role.OWNER)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session[ACTIVE_SHOP_SESSION_KEY], shop.id)

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(username="existing", email="maker@example.com")

        response = self.client.post(
            reverse("signup"),
            {
                "username": "other",
                "email": "MAKER@example.com",
                "workspace_name": "Other Studio",
                "shop_name": "Other Gifts",
                "password1": "strong-start-pass-123",
                "password2": "strong-start-pass-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists")
        self.assertFalse(Workspace.objects.filter(name="Other Studio").exists())
