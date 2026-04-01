from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def _calculate_category_client_fees(offer, details):
    categories = details.get("categories", [])
    sub_categories = details.get("subCategories", [])
    offers_list = details.get("offers", [])
    external_markup_map = details.get("categoryExternalMarkup", {})

    prod_insurance = Decimal(str(offer.production_insurance_percent or 0))
    prod_fee = Decimal(str(offer.production_fee_percent or 0))
    post_markup = Decimal(str(offer.post_markup_percent or 0))
    post_insurance = Decimal(str(offer.post_insurance_percent or 0))
    post_tax = Decimal(str(offer.post_tax_percent or 0))

    total_client_fees = Decimal("0")

    for cat in categories:
        cat_id = cat.get("id", "")
        tags = cat.get("tags", [])

        direct = [x for x in offers_list if x.get("categoryId") == cat_id]
        client_sum = Decimal(str(sum(x.get("clientCost", 0) for x in direct)))

        sub_ids = [
            s.get("id") for s in sub_categories if s.get("parentCategoryId") == cat_id
        ]
        for sub_id in sub_ids:
            sub_offers = [x for x in offers_list if x.get("categoryId") == sub_id]
            client_sum += Decimal(str(sum(x.get("clientCost", 0) for x in sub_offers)))

        ext = (
            external_markup_map.get(cat_id)
            if isinstance(external_markup_map, dict)
            else None
        )
        has_ext = bool(ext and ext.get("enabled") and (ext.get("percent") or 0) > 0)

        if "production" in tags:
            if prod_insurance > 0:
                total_client_fees += client_sum * prod_insurance / 100
            if prod_fee > 0 and not has_ext:
                total_client_fees += client_sum * prod_fee / 100

        if "post_production" in tags:
            if post_insurance > 0:
                total_client_fees += client_sum * post_insurance / 100
            if post_markup > 0 and not has_ext:
                total_client_fees += client_sum * post_markup / 100
            if post_tax > 0:
                total_client_fees += client_sum * post_tax / 100

        if has_ext:
            ext_percent = Decimal(str(ext.get("percent", 0)))
            total_client_fees += client_sum * ext_percent / 100

    return total_client_fees


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
            cost = base_cost + base_cost * uf_percent / 100
        else:
            cost = base_cost

        client_fees = _calculate_category_client_fees(offer, details)
        client_total = base_client_cost + client_fees

        offer.cost = cost
        offer.profit = client_total - cost
        offer.save(update_fields=["cost", "profit", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0016_recalculate_offer_totals"),
    ]

    operations = [
        migrations.RunPython(
            recalculate_all_offer_totals,
            migrations.RunPython.noop,
        ),
    ]
