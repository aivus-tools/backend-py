"""Management command to migrate existing Offer.details JSON into OfferEntry records."""

import logging

from django.core.management.base import BaseCommand

from aivus_backend.projects.models import Offer
from aivus_backend.projects.services import parse_offer_details_to_entries

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Parse all existing Offer.details JSON into OfferEntry records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be migrated without making changes.",
        )
        parser.add_argument(
            "--offer-id",
            type=str,
            help="Migrate a specific offer by UUID.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        offer_id = options.get("offer_id")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        queryset = Offer.objects.filter(deleted_at__isnull=True)
        if offer_id:
            queryset = queryset.filter(id=offer_id)

        total = queryset.count()
        self.stdout.write(f"Found {total} offers to process")

        success = 0
        skipped = 0
        errors = 0

        for offer in queryset.iterator():
            details = offer.details

            if not details or not isinstance(details, dict):
                self.stdout.write(f"  SKIP {offer.id}: no details or not a dict")
                skipped += 1
                continue

            offers_list = details.get("offers", [])
            if not offers_list:
                self.stdout.write(f"  SKIP {offer.id}: no 'offers' array in details")
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(
                    f"  WOULD MIGRATE {offer.id} ({offer.project_name}): "
                    f"{len(offers_list)} line items"
                )
                success += 1
                continue

            try:
                count = parse_offer_details_to_entries(offer, details)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  MIGRATED {offer.id} ({offer.project_name}): "
                        f"{count} entries created"
                    )
                )
                success += 1
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  ERROR {offer.id}: {e}")
                )
                logger.exception("Error migrating offer %s", offer.id)
                errors += 1

        self.stdout.write("")
        action = "Would migrate" if dry_run else "Migrated"
        self.stdout.write(self.style.SUCCESS(f"{action}: {success}"))
        self.stdout.write(f"Skipped: {skipped}")
        if errors:
            self.stdout.write(self.style.ERROR(f"Errors: {errors}"))
        self.stdout.write(f"Total: {total}")
