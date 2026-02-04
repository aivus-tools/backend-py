"""Catalog models: Category, Entry, Unit, EntryUnit."""

import uuid

from django.db import models

from aivus_backend.core.enums import UnitDimension


class Category(models.Model):
    """Category model with self-referencing hierarchy."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_category = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=255)
    level = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "category"
        ordering = ["level", "name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name

    def get_full_path(self):
        """Get category path: Parent > Child > Grandchild."""
        path = [self.name]
        parent = self.parent_category
        while parent:
            path.insert(0, parent.name)
            parent = parent.parent_category
        return " > ".join(path)


class Unit(models.Model):
    """Unit of measurement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    symbol = models.CharField(max_length=50)
    dimension = models.CharField(max_length=20, choices=UnitDimension.choices)
    is_default = models.BooleanField(default=False, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "unit"
        ordering = ["name"]

    def __str__(self):
        if self.name in ["Flat", "Each"]:
            return f"{self.name}"
        return f"{self.name} (s)"

    def save(self, *args, **kwargs):
        if self.is_default:
            # Set all other units' is_default to False
            Unit.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class Entry(models.Model):
    """Entry/Position in catalog."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    short_description = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_approved = models.BooleanField(default=False)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "entry"
        ordering = ["-created_at"]
        verbose_name_plural = "Entries"

    def __str__(self):
        return self.name


class EntryUnit(models.Model):
    """Many-to-many relationship between Entry and Unit."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(
        Entry,
        on_delete=models.CASCADE,
        related_name="entry_units",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="entry_units",
    )
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "entry_unit"
        unique_together = [["entry", "unit"]]
        ordering = ["-is_default", "unit__name"]

    def save(self, *args, **kwargs):
        if self.is_default:
            # Set all other entry units for this entry to is_default=False
            EntryUnit.objects.filter(entry=self.entry).exclude(pk=self.pk).update(
                is_default=False
            )
        super().save(*args, **kwargs)

    def __str__(self):
        default = " (default)" if self.is_default else ""
        return f"{self.entry.name} - {self.unit.symbol}{default}"
