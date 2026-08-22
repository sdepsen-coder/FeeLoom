import os

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or promote a FeeLoom administrator from one-time environment values."

    def handle(self, *args, **options):
        enabled = os.environ.get("FEELOOM_BOOTSTRAP_ADMIN_ON_START", "false").lower()
        if enabled not in {"1", "true", "yes", "on"}:
            self.stdout.write("Admin bootstrap disabled.")
            return

        values = {
            "username": os.environ.get("FEELOOM_BOOTSTRAP_ADMIN_USERNAME", "").strip(),
            "email": os.environ.get("FEELOOM_BOOTSTRAP_ADMIN_EMAIL", "").strip(),
            "password": os.environ.get("FEELOOM_BOOTSTRAP_ADMIN_PASSWORD", ""),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise CommandError(f"Missing admin bootstrap values: {', '.join(missing)}")

        candidate = User(username=values["username"], email=values["email"])
        try:
            validate_password(values["password"], user=candidate)
        except ValidationError as exc:
            raise CommandError("Admin bootstrap password does not meet security rules.") from exc

        user, created = User.objects.get_or_create(username=values["username"])
        user.email = values["email"]
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(values["password"])
        user.save()

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} administrator {user.username}."))
