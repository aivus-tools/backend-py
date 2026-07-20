"""Re-encrypt every EncryptedTextField value onto the current primary key.

Run after prepending a new key to ``FERNET_KEYS``. MultiFernet decrypts with
any key but only re-encrypts to the primary when the row is re-saved, so this
walks every encrypted field and rewrites it. Safe to run repeatedly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

from aivus_backend.email_agent.crypto import EncryptedTextField

if TYPE_CHECKING:
    from argparse import ArgumentParser

    from django.db.models import Model


class Command(BaseCommand):
    help = "Re-encrypt all EncryptedTextField values onto the primary FERNET key."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--dry-run", action="store_true", default=False)

    def handle(self, *args: object, **options: object) -> None:
        dry_run = bool(options.get("dry_run"))
        total = 0
        for model in apps.get_models():
            field_names = [
                field.name
                for field in model._meta.get_fields()  # noqa: SLF001
                if isinstance(field, EncryptedTextField)
            ]
            if not field_names:
                continue
            count = self._rewrite(model, field_names, dry_run=dry_run)
            total += count
            self.stdout.write(f"{model._meta.label}: {count} rows")  # noqa: SLF001
        verb = "would re-encrypt" if dry_run else "re-encrypted"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} rows"))

    def _rewrite(
        self, model: type[Model], field_names: list[str], *, dry_run: bool
    ) -> int:
        count = 0
        with transaction.atomic():
            for instance in model._default_manager.all().iterator():  # noqa: SLF001
                if dry_run:
                    count += 1
                    continue
                instance.save(update_fields=field_names)
                count += 1
        return count
