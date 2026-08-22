from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings

from .views import server_error


@override_settings(DEBUG=False)
class ErrorPageTests(TestCase):
    def test_unknown_page_uses_branded_404(self):
        response = self.client.get("/this-page-does-not-exist/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, "FeeLoom", status_code=404)
        self.assertNotContains(response, "Using the URLconf", status_code=404)

    def test_permission_denied_uses_branded_403(self):
        user = User.objects.create_user(username="restricted-user", password="pass-12345")
        self.client.force_login(user)

        response = self.client.get("/system-status/")

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access denied", status_code=403)
        self.assertContains(response, "Back to overview", status_code=403)

    def test_server_error_does_not_require_application_context(self):
        request = RequestFactory().get("/broken/")
        request.user = User()

        response = server_error(request)

        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Something went wrong", response.content)
        self.assertNotIn(b"Traceback", response.content)
