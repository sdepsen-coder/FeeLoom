import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.utils.connection import ConnectionDoesNotExist


class Command(BaseCommand):
    help = "Create a consistent SQLite or PostgreSQL database backup."

    def add_arguments(self, parser):
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)
        parser.add_argument("--output")
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace an existing backup file.",
        )

    def handle(self, *args, **options):
        database = options["database"]
        try:
            connection = connections[database]
        except ConnectionDoesNotExist as exc:
            raise CommandError(f"Unknown database alias: {database}") from exc

        extension = ".sqlite3" if connection.vendor == "sqlite" else ".dump"
        output = options["output"] or self._default_output(extension)
        destination = Path(output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists() and not options["overwrite"]:
            raise CommandError(
                f"Backup already exists: {destination}. Use --overwrite to replace it."
            )

        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.unlink(missing_ok=True)
        try:
            if connection.vendor == "sqlite":
                self._backup_sqlite(connection, temporary)
            elif connection.vendor == "postgresql":
                self._backup_postgresql(connection, temporary)
            else:
                raise CommandError(
                    f"Database vendor '{connection.vendor}' is not supported."
                )
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        self.stdout.write(self.style.SUCCESS(f"Backup created: {destination}"))

    @staticmethod
    def _default_output(extension):
        directory = Path(os.environ.get("FEELOOM_BACKUP_DIR", "backups"))
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        return directory / f"feeloom-{timestamp}{extension}"

    @staticmethod
    def _backup_sqlite(connection, destination):
        connection.ensure_connection()
        backup_connection = sqlite3.connect(destination)
        try:
            connection.connection.backup(backup_connection)
        finally:
            backup_connection.close()

    @staticmethod
    def _backup_postgresql(connection, destination):
        executable = shutil.which("pg_dump")
        if not executable:
            raise CommandError(
                "pg_dump is required for PostgreSQL backups and was not found."
            )

        settings = connection.settings_dict
        command = [
            executable,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={destination}",
        ]
        for option, key in (
            ("--host", "HOST"),
            ("--port", "PORT"),
            ("--username", "USER"),
        ):
            if settings.get(key):
                command.append(f"{option}={settings[key]}")
        command.append(settings["NAME"])

        environment = os.environ.copy()
        if settings.get("PASSWORD"):
            environment["PGPASSWORD"] = settings["PASSWORD"]
        sslmode = settings.get("OPTIONS", {}).get("sslmode")
        if sslmode:
            environment["PGSSLMODE"] = sslmode

        result = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            message = result.stderr.strip() or "pg_dump failed."
            raise CommandError(message)
