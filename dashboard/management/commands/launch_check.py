from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@dataclass(frozen=True)
class LaunchCheck:
    status: str
    label: str
    detail: str


class Command(BaseCommand):
    help = "Check whether FeeLoom is configured for a production beta launch."

    def add_arguments(self, parser):
        parser.add_argument(
            "--production",
            action="store_true",
            help="Require production-grade security and infrastructure settings.",
        )
        parser.add_argument(
            "--no-fail",
            action="store_true",
            help="Print blockers without returning a failing exit status.",
        )

    def handle(self, *args, **options):
        checks = self._checks(production=options["production"])
        styles = {
            "PASS": self.style.SUCCESS,
            "WARN": self.style.WARNING,
            "BLOCK": self.style.ERROR,
        }
        for check in checks:
            self.stdout.write(
                styles[check.status](f"{check.status:<5} {check.label}: {check.detail}")
            )

        blockers = [check for check in checks if check.status == "BLOCK"]
        warnings = [check for check in checks if check.status == "WARN"]
        self.stdout.write(
            f"Summary: {len(checks) - len(blockers) - len(warnings)} passed, "
            f"{len(warnings)} warnings, {len(blockers)} blockers."
        )
        if blockers and not options["no_fail"]:
            raise CommandError("FeeLoom is not ready for production beta launch.")

    def _checks(self, *, production):
        checks = []
        checks.extend(self._database_checks(production))
        checks.extend(self._security_checks(production))
        checks.extend(self._service_checks(production))
        return checks

    @staticmethod
    def _database_checks(production):
        checks = []
        try:
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks.append(LaunchCheck("PASS", "Database", "Connection succeeded"))
        except Exception as exc:
            return [
                LaunchCheck(
                    "BLOCK",
                    "Database",
                    f"Connection failed ({type(exc).__name__})",
                )
            ]

        if production and connection.vendor != "postgresql":
            checks.append(
                LaunchCheck("BLOCK", "Database engine", "PostgreSQL is required")
            )
        else:
            checks.append(
                LaunchCheck("PASS", "Database engine", connection.vendor)
            )

        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            checks.append(
                LaunchCheck("BLOCK", "Migrations", f"{len(pending)} pending")
            )
        else:
            checks.append(LaunchCheck("PASS", "Migrations", "Up to date"))
        return checks

    @staticmethod
    def _security_checks(production):
        checks = []
        if production and settings.DEBUG:
            checks.append(LaunchCheck("BLOCK", "Debug mode", "Must be disabled"))
        else:
            checks.append(
                LaunchCheck(
                    "PASS",
                    "Debug mode",
                    "Disabled" if not settings.DEBUG else "Development mode",
                )
            )

        weak_secret = (
            settings.SECRET_KEY.startswith("django-insecure-")
            or len(settings.SECRET_KEY) < 50
            or len(set(settings.SECRET_KEY)) < 5
        )
        if production and weak_secret:
            checks.append(
                LaunchCheck("BLOCK", "Django secret", "Use a strong generated value")
            )
        else:
            checks.append(LaunchCheck("PASS", "Django secret", "Configured"))

        if production and not settings.FEELOOM_TOKEN_ENCRYPTION_KEY:
            checks.append(
                LaunchCheck(
                    "BLOCK",
                    "Token encryption",
                    "Set a separate persistent encryption key",
                )
            )
        else:
            checks.append(
                LaunchCheck(
                    "PASS" if settings.FEELOOM_TOKEN_ENCRYPTION_KEY else "WARN",
                    "Token encryption",
                    "Configured"
                    if settings.FEELOOM_TOKEN_ENCRYPTION_KEY
                    else "Using the development fallback",
                )
            )
        return checks

    @staticmethod
    def _service_checks(production):
        checks = []
        if settings.FEELOOM_SUPPORT_EMAIL:
            checks.append(LaunchCheck("PASS", "Support email", "Configured"))
        else:
            checks.append(
                LaunchCheck("WARN", "Support email", "Add a public support address")
            )

        email_ready = (
            production_email_ready() if production else settings.EMAIL_DELIVERY_ENABLED
        )
        if email_ready:
            checks.append(
                LaunchCheck("PASS", "Account email", "Password reset is enabled")
            )
        else:
            checks.append(
                LaunchCheck(
                    "WARN",
                    "Account email",
                    "Password reset and verification are unavailable",
                )
            )

        etsy_key = bool(settings.ETSY_API_KEY)
        etsy_secret = bool(settings.ETSY_SHARED_SECRET)
        if etsy_key and etsy_secret:
            redirect = settings.ETSY_REDIRECT_URI
            secure_redirect = urlparse(redirect).scheme == "https"
            checks.append(LaunchCheck("PASS", "Etsy credentials", "Configured"))
            checks.append(
                LaunchCheck(
                    "PASS" if secure_redirect else "BLOCK",
                    "Etsy callback",
                    "Uses HTTPS" if secure_redirect else "Production callback must use HTTPS",
                )
            )
        elif etsy_key or etsy_secret:
            checks.append(
                LaunchCheck("BLOCK", "Etsy credentials", "Configuration is incomplete")
            )
        else:
            checks.append(
                LaunchCheck(
                    "WARN",
                    "Etsy credentials",
                    "CSV beta works, direct connection is unavailable",
                )
            )

        checks.append(
            LaunchCheck(
                "PASS" if settings.SENTRY_DSN else "WARN",
                "Error monitoring",
                "Sentry is configured" if settings.SENTRY_DSN else "Sentry is optional",
            )
        )
        if settings.FEELOOM_BACKUP_READY:
            checks.append(
                LaunchCheck("PASS", "Backups", "Backup and restore plan confirmed")
            )
        else:
            checks.append(
                LaunchCheck(
                    "BLOCK" if production else "WARN",
                    "Backups",
                    "Confirm backups and a tested restore before storing real seller data",
                )
            )
        return checks


def production_checks():
    return Command()._checks(production=True)


def production_email_ready():
    return bool(
        settings.EMAIL_HOST
        and settings.EMAIL_HOST_USER
        and settings.EMAIL_HOST_PASSWORD
        and settings.CONFIGURED_FROM_EMAIL
    )
