import pytest
from django.utils import timezone

from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="manager-test@example.com",
        password="testpass123",
        name="Manager Test User",
        group="VENDOR",
    )


@pytest.fixture
def vendor(user):
    return Vendor.objects.create(name="Manager Test Vendor", owner=user)


@pytest.fixture
def active_project(vendor):
    return Project.objects.create(name="Active Project", vendor=vendor)


@pytest.fixture
def deleted_project(vendor):
    now = timezone.now()
    project = Project.objects.create(name="Deleted Project", vendor=vendor)
    Project.objects.all_with_deleted().filter(pk=project.pk).update(deleted_at=now)
    return Project.objects.all_with_deleted().get(pk=project.pk)


@pytest.mark.django_db
class TestJournalizeManager:
    def test_default_queryset_excludes_soft_deleted(
        self, active_project, deleted_project
    ):
        projects = Project.objects.all()
        assert active_project in projects
        assert deleted_project not in projects

    def test_all_with_deleted_includes_deleted(self, active_project, deleted_project):
        projects = Project.objects.all_with_deleted()
        assert active_project in projects
        assert deleted_project in projects

    def test_deleted_only_returns_deleted(self, active_project, deleted_project):
        projects = Project.objects.deleted_only()
        assert active_project not in projects
        assert deleted_project in projects

    def test_delete_sets_deleted_at(self, active_project):
        assert active_project.deleted_at is None
        Project.objects.filter(pk=active_project.pk).delete()

        project = Project.objects.all_with_deleted().get(pk=active_project.pk)
        assert project.deleted_at is not None

    def test_delete_does_not_hard_delete(self, active_project):
        Project.objects.filter(pk=active_project.pk).delete()
        assert Project.objects.all_with_deleted().filter(pk=active_project.pk).exists()

    def test_hard_delete_removes_from_db(self, active_project):
        Project.objects.all_with_deleted().filter(pk=active_project.pk).hard_delete()
        assert (
            not Project.objects.all_with_deleted().filter(pk=active_project.pk).exists()
        )

    def test_alive_filter(self, active_project, deleted_project):
        alive = Project.objects.all_with_deleted().alive()
        assert active_project in alive
        assert deleted_project not in alive

    def test_deleted_filter(self, active_project, deleted_project):
        deleted = Project.objects.all_with_deleted().deleted()
        assert active_project not in deleted
        assert deleted_project in deleted

    def test_multiple_soft_deletes(self, vendor):
        project1 = Project.objects.create(name="Project 1", vendor=vendor)
        project2 = Project.objects.create(name="Project 2", vendor=vendor)
        project3 = Project.objects.create(name="Project 3", vendor=vendor)

        Project.objects.filter(pk__in=[project1.pk, project2.pk]).delete()

        assert Project.objects.count() == 1
        remaining = Project.objects.first()
        assert remaining is not None
        assert remaining.pk == project3.pk
        assert Project.objects.all_with_deleted().count() == 3
        assert Project.objects.deleted_only().count() == 2
