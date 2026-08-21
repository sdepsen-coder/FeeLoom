from django.core.management.base import BaseCommand

from integrations.models import EtsyConnection
from integrations.sync import sync_connection


class Command(BaseCommand):
    help = "Sync orders for all active Etsy connections."

    def add_arguments(self, parser):
        parser.add_argument("--shop-id", type=int)

    def handle(self, *args, **options):
        connections = EtsyConnection.objects.filter(is_active=True).select_related("shop")
        if options["shop_id"]:
            connections = connections.filter(shop_id=options["shop_id"])
        succeeded = 0
        failed = 0
        for connection in connections:
            try:
                run = sync_connection(connection)
                succeeded += 1
                self.stdout.write(
                    f"{connection.shop.name}: {run.imported_orders} new, {run.updated_orders} updated"
                )
            except Exception as exc:
                failed += 1
                self.stderr.write(f"{connection.shop.name}: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Etsy sync finished: {succeeded} succeeded, {failed} failed"))
