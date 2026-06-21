"""Tests for the 0038 dedup data migration (MF-3).

The migration soft-deletes duplicate active (vendor, brief) projects before the
unique constraint is added, so a prod database with pre-existing duplicates does
not fail AddConstraint. The constraint is already on the table in tests, so each
test drops it first to recreate the pre-migration duplicate state, runs the dedup
function, then asserts exactly one active project survives (the newest).
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as global_apps
from django.db import connection

from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

CONSTRAINT_NAME = "uniq_active_project_per_vendor_brief"

_migration = importlib.import_module(
    "aivus_backend.projects.migrations."
    "0038_project_uniq_active_project_per_vendor_brief"
)


def _drop_unique_constraint() -> None:
    with connection.cursor() as cursor:
        cursor.execute(f'DROP INDEX IF EXISTS "{CONSTRAINT_NAME}"')


@pytest.fixture
def vendor(db):
    user = User.objects.create_user(
        email="dedup-vendor@example.com",
        password="p@ssw0rd",
        name="Dedup Vendor",
        group="VENDOR",
    )
    return Vendor.objects.create(name="Dedup Studio", owner=user)


def _set_created_at(project: Project, when) -> None:
    """created_at is auto_now_add, so bypass it with a direct UPDATE."""
    Project.objects.filter(id=project.id).update(created_at=when)


@pytest.mark.django_db
def test_dedup_keeps_newest_and_soft_deletes_rest(vendor):
    brief = Brief.objects.create(client=None)
    _drop_unique_constraint()

    older = Project.objects.create(
        vendor=vendor, brief=brief, name="older", status=ProjectStatus.DRAFT
    )
    middle = Project.objects.create(
        vendor=vendor, brief=brief, name="middle", status=ProjectStatus.RFP
    )
    newest = Project.objects.create(
        vendor=vendor, brief=brief, name="newest", status=ProjectStatus.DRAFT
    )
    _set_created_at(older, "2026-01-01T00:00:00Z")
    _set_created_at(middle, "2026-02-01T00:00:00Z")
    _set_created_at(newest, "2026-03-01T00:00:00Z")

    _migration.dedup_active_projects(global_apps, None)

    survivors = list(
        Project.objects.filter(vendor=vendor, brief=brief, deleted_at__isnull=True)
    )
    assert len(survivors) == 1
    assert survivors[0].id == newest.id

    older.refresh_from_db()
    middle.refresh_from_db()
    assert older.deleted_at is not None
    assert middle.deleted_at is not None


@pytest.mark.django_db
def test_dedup_leaves_single_active_project_untouched(vendor):
    brief = Brief.objects.create(client=None)
    only = Project.objects.create(
        vendor=vendor, brief=brief, name="only", status=ProjectStatus.DRAFT
    )

    _migration.dedup_active_projects(global_apps, None)

    only.refresh_from_db()
    assert only.deleted_at is None


@pytest.mark.django_db
def test_dedup_is_per_vendor_brief_pair(vendor):
    """Duplicates in one pair must not affect a different pair's lone project."""
    brief_a = Brief.objects.create(client=None)
    brief_b = Brief.objects.create(client=None)
    _drop_unique_constraint()

    a_old = Project.objects.create(
        vendor=vendor, brief=brief_a, name="a-old", status=ProjectStatus.DRAFT
    )
    a_new = Project.objects.create(
        vendor=vendor, brief=brief_a, name="a-new", status=ProjectStatus.DRAFT
    )
    _set_created_at(a_old, "2026-01-01T00:00:00Z")
    _set_created_at(a_new, "2026-02-01T00:00:00Z")
    b_only = Project.objects.create(
        vendor=vendor, brief=brief_b, name="b-only", status=ProjectStatus.DRAFT
    )

    _migration.dedup_active_projects(global_apps, None)

    a_old.refresh_from_db()
    a_new.refresh_from_db()
    b_only.refresh_from_db()
    assert a_old.deleted_at is not None
    assert a_new.deleted_at is None
    assert b_only.deleted_at is None


@pytest.mark.django_db
def test_dedup_ignores_already_soft_deleted(vendor):
    """A pair with one active and one already soft-deleted project is not a
    duplicate (only one active row), so nothing changes."""
    from django.utils import timezone

    brief = Brief.objects.create(client=None)
    _drop_unique_constraint()
    active = Project.objects.create(
        vendor=vendor, brief=brief, name="active", status=ProjectStatus.RFP
    )
    deleted = Project.objects.create(
        vendor=vendor, brief=brief, name="deleted", status=ProjectStatus.DRAFT
    )
    Project.objects.filter(id=deleted.id).update(deleted_at=timezone.now())

    _migration.dedup_active_projects(global_apps, None)

    active.refresh_from_db()
    assert active.deleted_at is None
