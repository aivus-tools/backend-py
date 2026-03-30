from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def recalculate_all_offer_totals(apps, schema_editor):
    Offer = apps.get_model("projects", "Offer")
    OfferEntry = apps.get_model("projects", "OfferEntry")

    offers = Offer.objects.filter(deleted_at__isnull=True)
    for offer in offers:
        entries_agg = OfferEntry.objects.filter(
            offer=offer, deleted_at__isnull=True
        ).aggregate(
            total_cost=Sum("cost"),
            total_client_cost=Sum("client_cost"),
        )
        base_cost = entries_agg["total_cost"] or Decimal("0")
        base_client_cost = entries_agg["total_client_cost"] or Decimal("0")

        details = offer.details if isinstance(offer.details, dict) else {}
        unforeseen = details.get("unforeseenExpenses", {})
        if unforeseen.get("isVisible", True):
            uf_percent = Decimal(str(unforeseen.get("percent", 0)))
            uf_client_percent = Decimal(str(unforeseen.get("clientPercent", 0)))
            cost = base_cost + base_cost * uf_percent / 100
            client_total = base_client_cost + base_client_cost * uf_client_percent / 100
        else:
            cost = base_cost
            client_total = base_client_cost

        offer.cost = cost
        offer.profit = client_total - cost
        offer.save(update_fields=["cost", "profit", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0015_add_overtime_to_offer_entry"),
    ]

    operations = [
        migrations.RunPython(
            recalculate_all_offer_totals,
            migrations.RunPython.noop,
        ),
    ]
