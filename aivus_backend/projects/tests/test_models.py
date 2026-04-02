from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.catalog.models import Unit
from aivus_backend.core.enums import BriefStatus
from aivus_backend.core.enums import OfferSource
from aivus_backend.core.enums import OfferStatus
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefOffer
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.models import ClientManager
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import OfferDeliverable
from aivus_backend.projects.models import OfferEntry
from aivus_backend.projects.models import OfferRate
from aivus_backend.projects.models import OfferScheduleEntry
from aivus_backend.projects.models import Project
from aivus_backend.projects.models import ProjectCollaborator
from aivus_backend.projects.models import Rate
from aivus_backend.projects.models import RateCard
from aivus_backend.projects.models import RateCardItem
from aivus_backend.projects.models import Share
from aivus_backend.projects.models import SimpleRate
from aivus_backend.projects.models import Template
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="test-vendor@example.com",
        password="testpass123",
        name="Test User",
        group="VENDOR",
    )


@pytest.fixture
def second_user(db):
    return User.objects.create_user(
        email="test-client@example.com",
        password="testpass123",
        name="Test Client User",
        group="CLIENT",
    )


@pytest.fixture
def vendor(user):
    return Vendor.objects.create(name="Test Vendor", owner=user)


@pytest.fixture
def second_vendor(second_user):
    return Vendor.objects.create(name="Second Vendor", owner=second_user)


@pytest.fixture
def client_entity(second_user):
    return Client.objects.create(
        name="Test Client", ein="12-3456789", owner=second_user
    )


@pytest.fixture
def category(db):
    return Category.objects.create(name="Production", level=1)


@pytest.fixture
def entry(category):
    return Entry.objects.create(name="Director", category=category, is_approved=True)


@pytest.fixture
def second_entry(category):
    return Entry.objects.create(
        name="Camera Operator", category=category, is_approved=True
    )


@pytest.fixture
def unit(db):
    return Unit.objects.create(name="Day", symbol="day", dimension="TEMPORAL")


@pytest.fixture
def project(vendor):
    return Project.objects.create(name="Test Project", vendor=vendor)


@pytest.fixture
def brief(client_entity):
    return Brief.objects.create(
        status=BriefStatus.DRAFT,
        details={"projectName": "Test Brief"},
        client=client_entity,
    )


@pytest.fixture
def offer(project):
    return Offer.objects.create(
        project_name="Test Offer",
        project=project,
        source=OfferSource.PLATFORM,
    )


@pytest.fixture
def rate(vendor, entry):
    return Rate(
        name="Director Day Rate",
        vendor=vendor,
        entry=entry,
        base_price=Decimal("1000.00"),
        total_price=Decimal("0"),
        options=[],
    )


@pytest.fixture
def rate_card(vendor):
    return RateCard.objects.create(name="Standard Rates", vendor=vendor)


@pytest.mark.django_db
class TestProject:
    def test_str(self, project, vendor):
        assert str(project) == f"Test Project ({vendor.name})"

    def test_restore(self, project):
        project.deleted_at = timezone.now()
        project.save(update_fields=("deleted_at",))
        assert project.deleted_at is not None

        project.restore()
        project.refresh_from_db()
        assert project.deleted_at is None

    def test_soft_delete_via_manager(self, project):
        Project.objects.filter(pk=project.pk).delete()
        assert Project.objects.filter(pk=project.pk).count() == 0
        assert Project.objects.all_with_deleted().filter(pk=project.pk).count() == 1

    def test_vendor_protect_on_delete(self, project, vendor, user):
        with pytest.raises(Exception):
            vendor.delete()

    def test_default_status(self, project):
        assert project.status == ProjectStatus.DRAFT


@pytest.mark.django_db
class TestProjectCollaborator:
    def test_creation_all_roles(self, project, user):
        roles = ["internal_user", "external_user", "producer", "agency_producer"]
        for role in roles:
            collaborator = ProjectCollaborator.objects.create(
                project=project,
                user=user,
                name=f"Collab {role}",
                role=role,
            )
            assert collaborator.role == role

    def test_str(self, project, user):
        collaborator = ProjectCollaborator.objects.create(
            project=project,
            user=user,
            name="John Doe",
            role="producer",
        )
        assert str(collaborator) == f"John Doe - {project.name}"

    def test_cascade_on_project_delete(self, project, user):
        ProjectCollaborator.objects.create(
            project=project,
            user=user,
            name="John Doe",
        )
        project_id = project.pk
        Project.objects.all_with_deleted().filter(pk=project_id).hard_delete()
        assert ProjectCollaborator.objects.count() == 0


@pytest.mark.django_db
class TestClientManager:
    def test_creation(self, project):
        manager = ClientManager.objects.create(
            project=project,
            name="Jane Smith",
            position="Marketing Director",
        )
        assert manager.name == "Jane Smith"
        assert manager.position == "Marketing Director"

    def test_str(self, project):
        manager = ClientManager.objects.create(
            project=project,
            name="Jane Smith",
            position="Marketing Director",
        )
        assert str(manager) == f"Jane Smith (Marketing Director) - {project.name}"

    def test_cascade_on_project_delete(self, project):
        ClientManager.objects.create(project=project, name="Jane Smith")
        Project.objects.all_with_deleted().filter(pk=project.pk).hard_delete()
        assert ClientManager.objects.count() == 0


@pytest.mark.django_db
class TestSimpleRate:
    def test_creation(self, vendor, entry):
        simple_rate = SimpleRate.objects.create(
            vendor=vendor,
            entry=entry,
            value=Decimal("500.00"),
        )
        assert simple_rate.value == Decimal("500.00")

    def test_str(self, vendor, entry):
        simple_rate = SimpleRate.objects.create(
            vendor=vendor,
            entry=entry,
            value=Decimal("500.00"),
        )
        assert str(simple_rate) == f"{vendor.name} - {entry.name}: $500.00"

    def test_unique_together(self, vendor, entry):
        SimpleRate.objects.create(vendor=vendor, entry=entry, value=Decimal("500.00"))
        with pytest.raises(IntegrityError):
            SimpleRate.objects.create(
                vendor=vendor, entry=entry, value=Decimal("600.00")
            )

    def test_decimal_value(self, vendor, entry):
        simple_rate = SimpleRate.objects.create(
            vendor=vendor,
            entry=entry,
            value=Decimal("1234.56"),
        )
        simple_rate.refresh_from_db()
        assert simple_rate.value == Decimal("1234.56")

    def test_cascade_on_vendor_delete(self, vendor, entry, user):
        SimpleRate.objects.create(vendor=vendor, entry=entry, value=Decimal("500.00"))
        Vendor.objects.filter(pk=vendor.pk).delete()
        assert SimpleRate.objects.count() == 0

    def test_cascade_on_entry_delete(self, vendor, entry):
        SimpleRate.objects.create(vendor=vendor, entry=entry, value=Decimal("500.00"))
        entry.delete()
        assert SimpleRate.objects.count() == 0


@pytest.mark.django_db
class TestRate:
    def test_creation(self, vendor, entry):
        rate = Rate(
            name="Director Day Rate",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        assert rate.name == "Director Day Rate"

    def test_str(self, vendor, entry):
        rate = Rate(
            name="Director Day Rate",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        assert str(rate) == "Director Day Rate - $1000.00"

    def test_calculate_total_price_fixed_options(self, vendor, entry):
        rate = Rate(
            name="Rate with fixed",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[
                {"name": "Equipment", "type": "fixed", "value": 200},
                {"name": "Travel", "type": "fixed", "value": 150},
            ],
        )
        total = rate.calculate_total_price()
        assert total == Decimal("1350.00")

    def test_calculate_total_price_percentage_options(self, vendor, entry):
        rate = Rate(
            name="Rate with percent",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[
                {"name": "Tax", "type": "percentage", "value": 10},
            ],
        )
        total = rate.calculate_total_price()
        assert total == Decimal("1100.00")

    def test_calculate_total_price_mixed(self, vendor, entry):
        rate = Rate(
            name="Mixed rate",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[
                {"name": "Equipment", "type": "fixed", "value": 500},
                {"name": "Tax", "type": "percentage", "value": 10},
            ],
        )
        total = rate.calculate_total_price()
        assert total == Decimal("1650.00")

    def test_auto_save_recalculates_total(self, vendor, entry):
        rate = Rate(
            name="Auto calc",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[{"name": "Fee", "type": "fixed", "value": 250}],
        )
        rate.save()
        rate.refresh_from_db()
        assert rate.total_price == Decimal("1250.00")

    def test_is_custom_true_when_entry_none(self, vendor):
        rate = Rate(
            name="Custom Rate",
            vendor=vendor,
            entry=None,
            base_price=Decimal("500.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        assert rate.is_custom is True

    def test_is_custom_false_when_entry_set(self, vendor, entry):
        rate = Rate(
            name="Catalog Rate",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("500.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        assert rate.is_custom is False


@pytest.mark.django_db
class TestOffer:
    def test_creation(self, offer):
        assert offer.project_name == "Test Offer"

    def test_str(self, offer):
        assert str(offer) == "Test Offer (DRAFT)"

    def test_default_values(self, project):
        offer = Offer.objects.create(
            project_name="Defaults Offer",
            project=project,
            source=OfferSource.PLATFORM,
        )
        assert offer.status == OfferStatus.DRAFT
        assert offer.cost == 0
        assert offer.profit == 0
        assert offer.details == {}
        assert offer.is_locked is False

    def test_all_percent_fields(self, project):
        offer = Offer.objects.create(
            project_name="Percent Offer",
            project=project,
            source=OfferSource.PLATFORM,
            fringes_percent=Decimal("10.50"),
            handling_percent=Decimal("5.25"),
            markup_percent=Decimal("15.00"),
            production_insurance_percent=Decimal("3.00"),
            production_fee_percent=Decimal("8.00"),
            post_markup_percent=Decimal("12.00"),
            post_insurance_percent=Decimal("2.50"),
            post_tax_percent=Decimal("7.00"),
        )
        offer.refresh_from_db()
        assert offer.fringes_percent == Decimal("10.50")
        assert offer.handling_percent == Decimal("5.25")
        assert offer.markup_percent == Decimal("15.00")
        assert offer.production_insurance_percent == Decimal("3.00")
        assert offer.production_fee_percent == Decimal("8.00")
        assert offer.post_markup_percent == Decimal("12.00")
        assert offer.post_insurance_percent == Decimal("2.50")
        assert offer.post_tax_percent == Decimal("7.00")


@pytest.mark.django_db
class TestOfferEntry:
    def test_creation(self, offer, entry, category):
        offer_entry = OfferEntry.objects.create(
            offer=offer,
            item_name="Director",
            entry=entry,
            category=category,
            price=Decimal("5000.00"),
            sort_order=1,
        )
        assert offer_entry.item_name == "Director"

    def test_str(self, offer):
        offer_entry = OfferEntry.objects.create(
            offer=offer,
            item_name="Camera Op",
            sort_order=0,
        )
        assert str(offer_entry) == f"{offer.project_name} - Camera Op"

    def test_str_fallback_to_frontend_id(self, offer):
        offer_entry = OfferEntry.objects.create(
            offer=offer,
            frontend_id="item-123",
            sort_order=0,
        )
        assert str(offer_entry) == f"{offer.project_name} - item-123"

    def test_cascade_on_offer_delete(self, offer):
        OfferEntry.objects.create(offer=offer, item_name="Director", sort_order=0)
        offer.delete()
        assert OfferEntry.objects.count() == 0

    def test_sort_order(self, offer):
        entry_a = OfferEntry.objects.create(
            offer=offer, item_name="B Item", sort_order=2
        )
        entry_b = OfferEntry.objects.create(
            offer=offer, item_name="A Item", sort_order=1
        )
        entries = list(OfferEntry.objects.filter(offer=offer))
        assert entries[0].pk == entry_b.pk
        assert entries[1].pk == entry_a.pk


@pytest.mark.django_db
class TestOfferRate:
    def test_creation(self, offer, vendor, entry):
        rate = Rate(
            name="Director Rate",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        offer_rate = OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            base_price=rate.base_price,
            total_price=rate.total_price,
            options=rate.options,
        )
        assert offer_rate.name == "Director Rate"

    def test_str(self, offer, vendor, entry):
        rate = Rate(
            name="Director Rate",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        offer_rate = OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            base_price=rate.base_price,
            total_price=rate.total_price,
            options=rate.options,
        )
        assert str(offer_rate) == f"{offer.project_name} - Director Rate"

    def test_unique_together(self, offer, vendor, entry):
        rate = Rate(
            name="Director Rate",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            base_price=rate.base_price,
            total_price=rate.total_price,
            options=rate.options,
        )
        with pytest.raises(IntegrityError):
            OfferRate.objects.create(
                offer=offer,
                rate=rate,
                name="Duplicate",
                base_price=Decimal("500.00"),
                total_price=Decimal("500.00"),
                options=[],
            )

    def test_snapshot_independence(self, offer, vendor, entry):
        rate = Rate(
            name="Original Name",
            vendor=vendor,
            entry=entry,
            base_price=Decimal("1000.00"),
            total_price=Decimal("0"),
            options=[],
        )
        rate.save()
        offer_rate = OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            base_price=rate.base_price,
            total_price=rate.total_price,
            options=rate.options,
        )

        rate.name = "Updated Name"
        rate.base_price = Decimal("2000.00")
        rate.save()

        offer_rate.refresh_from_db()
        assert offer_rate.name == "Original Name"
        assert offer_rate.base_price == Decimal("1000.00")


@pytest.mark.django_db
class TestOfferDeliverable:
    def test_creation(self, offer):
        deliverable = OfferDeliverable.objects.create(
            offer=offer,
            quantity=2,
            duration="30",
            duration_unit="Sec",
            notes="Final cut",
        )
        assert deliverable.quantity == 2

    def test_str(self, offer):
        deliverable = OfferDeliverable.objects.create(
            offer=offer,
            quantity=3,
            duration="60",
            duration_unit="Sec",
        )
        assert str(deliverable) == f"{offer.project_name} - 3x 60Sec"

    def test_cascade_on_offer_delete(self, offer):
        OfferDeliverable.objects.create(offer=offer, quantity=1)
        offer.delete()
        assert OfferDeliverable.objects.count() == 0


@pytest.mark.django_db
class TestOfferScheduleEntry:
    def test_creation(self, offer):
        schedule = OfferScheduleEntry.objects.create(
            offer=offer,
            phase_type="Shoot",
            days=3,
            hours_per_day=10,
            notes="On location",
        )
        assert schedule.phase_type == "Shoot"
        assert schedule.days == 3

    def test_str(self, offer):
        schedule = OfferScheduleEntry.objects.create(
            offer=offer,
            phase_type="Post",
            days=5,
            hours_per_day=8,
        )
        assert str(schedule) == f"{offer.project_name} - Post (5d @ 8h)"

    def test_cascade_on_offer_delete(self, offer):
        OfferScheduleEntry.objects.create(offer=offer, phase_type="Prep", days=1)
        offer.delete()
        assert OfferScheduleEntry.objects.count() == 0


@pytest.mark.django_db
class TestShare:
    def test_creation(self, offer, user):
        share = Share.objects.create(offer=offer, created_by=user)
        assert share.offer == offer
        assert share.created_by == user

    def test_str(self, offer, user):
        share = Share.objects.create(offer=offer, created_by=user)
        token_prefix = share.token[:8]
        assert str(share) == f"{offer.project_name} - token:{token_prefix}... (active)"

    def test_str_inactive(self, offer, user):
        share = Share.objects.create(offer=offer, created_by=user, is_active=False)
        token_prefix = share.token[:8]
        assert (
            str(share) == f"{offer.project_name} - token:{token_prefix}... (inactive)"
        )

    def test_token_auto_generation(self, offer, user):
        share = Share.objects.create(offer=offer, created_by=user)
        assert share.token is not None
        assert len(share.token) > 0

    def test_tokens_are_unique(self, offer, user):
        share1 = Share.objects.create(offer=offer, created_by=user)
        share2 = Share.objects.create(offer=offer, created_by=user)
        assert share1.token != share2.token

    def test_is_active_default(self, offer, user):
        share = Share.objects.create(offer=offer, created_by=user)
        assert share.is_active is True


@pytest.mark.django_db
class TestBriefOffer:
    def test_creation(self, brief, offer, user):
        brief_offer = BriefOffer.objects.create(
            brief=brief,
            offer=offer,
            linked_by=user,
        )
        assert brief_offer.brief == brief
        assert brief_offer.offer == offer

    def test_str(self, brief, offer):
        brief_offer = BriefOffer.objects.create(brief=brief, offer=offer)
        assert str(brief_offer) == f"Brief {brief.pk} <-> Offer {offer.project_name}"

    def test_unique_together(self, brief, offer):
        BriefOffer.objects.create(brief=brief, offer=offer)
        with pytest.raises(IntegrityError):
            BriefOffer.objects.create(brief=brief, offer=offer)

    def test_cascade_on_brief_delete(self, brief, offer):
        BriefOffer.objects.create(brief=brief, offer=offer)
        brief.delete()
        assert BriefOffer.objects.count() == 0

    def test_cascade_on_offer_delete(self, brief, offer):
        BriefOffer.objects.create(brief=brief, offer=offer)
        offer.delete()
        assert BriefOffer.objects.count() == 0


@pytest.mark.django_db
class TestTemplate:
    def test_creation(self, vendor, offer):
        template = Template.objects.create(
            name="Commercial Template",
            vendor=vendor,
            source_offer=offer,
            details={"sections": [{"name": "Production"}]},
        )
        assert template.name == "Commercial Template"

    def test_str(self, vendor, offer):
        template = Template.objects.create(
            name="Commercial Template",
            vendor=vendor,
            source_offer=offer,
        )
        assert str(template) == f"Commercial Template ({vendor.name})"

    def test_details_json_storage(self, vendor):
        details = {
            "sections": [
                {"name": "Pre-Production", "items": ["Script", "Storyboard"]},
                {"name": "Production", "items": ["Crew", "Equipment"]},
            ],
            "totals": {"subtotal": 10000, "tax": 800},
        }
        template = Template.objects.create(
            name="JSON Template",
            vendor=vendor,
            details=details,
        )
        template.refresh_from_db()
        assert template.details == details

    def test_set_null_on_source_offer_delete(self, vendor, offer):
        template = Template.objects.create(
            name="Orphan Template",
            vendor=vendor,
            source_offer=offer,
        )
        offer.delete()
        template.refresh_from_db()
        assert template.source_offer is None


@pytest.mark.django_db
class TestRateCard:
    def test_creation(self, rate_card, vendor):
        assert rate_card.name == "Standard Rates"
        assert rate_card.vendor == vendor

    def test_str(self, rate_card, vendor):
        assert str(rate_card) == f"Standard Rates ({vendor.name})"

    def test_cascade_on_vendor_delete(self, rate_card, vendor):
        Vendor.objects.filter(pk=vendor.pk).delete()
        assert RateCard.objects.count() == 0


@pytest.mark.django_db
class TestRateCardItem:
    def test_creation(self, rate_card, entry, unit):
        item = RateCardItem.objects.create(
            rate_card=rate_card,
            entry=entry,
            item_name="Director",
            price=Decimal("1500.00"),
            unit=unit,
            unit_label="day",
        )
        assert item.item_name == "Director"
        assert item.price == Decimal("1500.00")

    def test_str(self, rate_card):
        item = RateCardItem.objects.create(
            rate_card=rate_card,
            item_name="Camera Op",
            price=Decimal("800.00"),
        )
        assert str(item) == "Camera Op - $800.00"

    def test_set_null_on_entry_delete(self, rate_card, entry):
        item = RateCardItem.objects.create(
            rate_card=rate_card,
            entry=entry,
            item_name="Director",
            price=Decimal("1500.00"),
        )
        entry.delete()
        item.refresh_from_db()
        assert item.entry is None

    def test_set_null_on_unit_delete(self, rate_card, unit):
        item = RateCardItem.objects.create(
            rate_card=rate_card,
            item_name="Director",
            price=Decimal("1500.00"),
            unit=unit,
        )
        unit.delete()
        item.refresh_from_db()
        assert item.unit is None

    def test_cascade_on_rate_card_delete(self, rate_card):
        RateCardItem.objects.create(
            rate_card=rate_card,
            item_name="Director",
            price=Decimal("1500.00"),
        )
        rate_card.delete()
        assert RateCardItem.objects.count() == 0


@pytest.mark.django_db
class TestChatMessage:
    def test_creation(self, brief, user):
        message = ChatMessage.objects.create(
            brief=brief,
            user=user,
            role="user",
            content="I need a 30-second commercial.",
        )
        assert message.content == "I need a 30-second commercial."

    def test_str_truncated(self, brief, user):
        long_content = "A" * 100
        message = ChatMessage.objects.create(
            brief=brief,
            user=user,
            role="assistant",
            content=long_content,
        )
        assert str(message) == f"assistant: {'A' * 50}..."

    def test_role_choices(self, brief, user):
        user_message = ChatMessage.objects.create(
            brief=brief, user=user, role="user", content="Hello"
        )
        assistant_message = ChatMessage.objects.create(
            brief=brief, user=user, role="assistant", content="Hi there"
        )
        assert user_message.role == "user"
        assert assistant_message.role == "assistant"


@pytest.mark.django_db
class TestBrief:
    def test_creation(self, brief, client_entity):
        assert brief.client == client_entity
        assert brief.details == {"projectName": "Test Brief"}

    def test_str(self, brief):
        assert str(brief) == f"Brief {brief.pk} - DRAFT"

    def test_default_status(self, client_entity):
        new_brief = Brief.objects.create(client=client_entity)
        assert new_brief.status == BriefStatus.DRAFT
