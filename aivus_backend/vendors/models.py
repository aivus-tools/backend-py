"""Vendor-related models."""

from typing import ClassVar

from django.db import models

from aivus_backend.core.enums import Language
from aivus_backend.core.managers import JournalizeManager
from aivus_backend.core.models import JournalizeModel


class PreVendor(JournalizeModel):
    """
    Curated marketing card for a recommended video production vendor.

    Shown to clients on the finalized brief screen so they can pick a vendor
    and send the brief by email. Filled in by admins through the Django admin
    and not connected to authenticated Vendor users.
    """

    objects: ClassVar[JournalizeManager] = JournalizeManager()

    logo = models.ImageField(upload_to="pre_vendor_logos/", null=True, blank=True)
    portfolio_url = models.URLField(blank=True, default="")
    title = models.CharField(max_length=255)
    short_description = models.TextField()
    language = models.CharField(
        max_length=5,
        choices=Language.choices,
        db_index=True,
    )
    address = models.CharField(max_length=500, blank=True, default="")
    email = models.EmailField()
    rank_label = models.CharField(max_length=64, blank=True, default="")
    category_label = models.CharField(max_length=128, blank=True, default="")
    sort_order = models.IntegerField(default=0, db_index=True)

    class Meta:
        db_table = "pre_vendor"
        ordering = ["sort_order", "-created_at"]
        verbose_name = "Pre-Vendor"
        verbose_name_plural = "Pre-Vendors"
        indexes = [
            models.Index(fields=["language", "sort_order"]),
        ]

    def __str__(self):
        return f"[{self.language}] {self.title}"
