"""Services for parsing/reconstructing Offer details JSON <-> OfferEntry records."""

import logging
from decimal import Decimal
from decimal import InvalidOperation

from django.db import transaction
from django.db.models import Sum

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import OfferEntry

logger = logging.getLogger(__name__)


def recalculate_offer_totals(offer):
    """Recalculate offer.cost and offer.profit from OfferEntry records.

    Aggregates cost and client_cost from all active entries,
    applies unforeseen expenses percentages from offer.details,
    and saves the result.
    """
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
        offer.cost = base_cost + base_cost * uf_percent / 100
        client_total = base_client_cost + base_client_cost * uf_client_percent / 100
    else:
        offer.cost = base_cost
        client_total = base_client_cost

    offer.profit = client_total - offer.cost
    offer.save(update_fields=["cost", "profit", "updated_at"])


def _to_decimal(value):
    """Safely convert a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _lookup_entry(entry_id):
    """Look up a catalog Entry by UUID, return None if not found."""
    if not entry_id:
        return None
    try:
        return Entry.objects.get(id=entry_id)
    except (Entry.DoesNotExist, ValueError, TypeError):
        return None


def _lookup_category(category_id):
    """Look up a catalog Category by UUID, return None if not found."""
    if not category_id:
        return None
    try:
        return Category.objects.get(id=category_id)
    except (Category.DoesNotExist, ValueError, TypeError):
        return None


def parse_offer_details_to_entries(offer, details_dict):
    """
    Parse the Offer.details JSON into OfferEntry records.

    The details_dict is expected to have a structure like:
    {
        "offers": [
            {
                "id": "frontend-uuid",
                "item": "Camera Operator",
                "entryId": "catalog-entry-uuid",
                "categoryId": "catalog-category-uuid",
                "price": 1500,
                "cost": 1200,
                "clientPrice": 1800,
                "clientCost": 1400,
                "surcharge": 20,
                "taxRate": 6,
                "taxPrice": 108,
                "showTax": false,
                "isLinkedSurcharge": true,
                "marketRange": "mid",
                "units": [...],
                "options": [...],
                ...
            },
            ...
        ],
        "surchargePercent": 20,
        "taxRate": 6,
        ...
    }

    This function:
    1. Deletes all existing OfferEntry records for this offer (full sync).
    2. Creates new OfferEntry records from details_dict["offers"].
    3. Stores top-level metadata (surcharge %, settings) in offer.metadata.
    4. Saves updated details JSON on the offer.
    """
    if not details_dict or not isinstance(details_dict, dict):
        logger.warning("parse_offer_details_to_entries: empty or invalid details for offer %s", offer.id)
        return

    offers_list = details_dict.get("offers", [])
    if not isinstance(offers_list, list):
        logger.warning("parse_offer_details_to_entries: 'offers' is not a list for offer %s", offer.id)
        return

    # QA2-022: Validate all items before deleting, and wrap in transaction
    # so rollback happens on any error
    for idx, item in enumerate(offers_list):
        if not isinstance(item, dict):
            logger.warning("Invalid non-dict item at index %d for offer %s", idx, offer.id)

    # QA3-037: Bulk pre-fetch entries and categories to avoid N+1 queries
    entry_ids = set()
    category_ids = set()
    for item in offers_list:
        if not isinstance(item, dict):
            continue
        if item.get("entryId"):
            entry_ids.add(item["entryId"])
        if item.get("categoryId"):
            category_ids.add(item["categoryId"])

    entries_by_id = {}
    if entry_ids:
        entries_by_id = {
            str(e.id): e for e in Entry.objects.filter(id__in=entry_ids)
        }
    categories_by_id = {}
    if category_ids:
        categories_by_id = {
            str(c.id): c for c in Category.objects.filter(id__in=category_ids)
        }

    with transaction.atomic():
        # Delete existing entries (full sync)
        deleted_count, _ = OfferEntry.objects.filter(offer=offer).delete()
        if deleted_count:
            logger.info("Deleted %d existing OfferEntry records for offer %s", deleted_count, offer.id)

        # Extract top-level metadata (everything except 'offers' array)
        metadata = {}
        for key, value in details_dict.items():
            if key != "offers":
                metadata[key] = value

        offer.metadata = metadata
        offer.details = details_dict
        offer.save(update_fields=["metadata", "details", "updated_at"])

        # Create OfferEntry records
        entries_created = 0
        for idx, item in enumerate(offers_list):
            if not isinstance(item, dict):
                continue

            # Collect extra data (units, options, and anything not in the direct mapping)
            mapped_keys = {
                "id", "item", "entryId", "categoryId", "price", "cost",
                "clientPrice", "clientCost", "surcharge", "taxRate", "taxPrice",
                "showTax", "isLinkedSurcharge", "marketRange",
            }
            item_data = {}
            for key, value in item.items():
                if key not in mapped_keys:
                    item_data[key] = value

            # QA3-037: Use pre-fetched lookups instead of per-item queries
            entry_ref = entries_by_id.get(str(item.get("entryId", "")))
            category_ref = categories_by_id.get(str(item.get("categoryId", "")))

            OfferEntry.objects.create(
                offer=offer,
                frontend_id=str(item.get("id", "")),
                item_name=item.get("item", ""),
                entry=entry_ref,
                category=category_ref,
                price=_to_decimal(item.get("price")),
                cost=_to_decimal(item.get("cost")),
                client_price=_to_decimal(item.get("clientPrice")),
                client_cost=_to_decimal(item.get("clientCost")),
                surcharge=_to_decimal(item.get("surcharge")),
                tax_rate=_to_decimal(item.get("taxRate")) or Decimal("0"),
                tax_price=_to_decimal(item.get("taxPrice")),
                show_tax=bool(item.get("showTax", False)),
                is_linked_surcharge=bool(item.get("isLinkedSurcharge", True)),
                market_range=str(item.get("marketRange", "")),
                item_data=item_data,
                sort_order=idx,
            )
            entries_created += 1

    logger.info(
        "Created %d OfferEntry records for offer %s",
        entries_created,
        offer.id,
    )
    return entries_created


def reconstruct_details_from_entries(offer):
    """
    Rebuild the details JSON dict from OfferEntry records.

    Returns the same structure that the frontend expects.
    Falls back to offer.details if no OfferEntry records exist.
    """
    # QA3-031: Convert to list first to avoid .exists() bypassing prefetch cache
    entries_list = list(offer.offer_entries.all().order_by("sort_order", "created_at"))

    if not entries_list:
        # No parsed entries yet - return raw details
        return offer.details

    offers_list = []
    for entry_record in entries_list:
        item = {}

        # Restore fields from item_data first (units, options, etc.)
        if entry_record.item_data:
            item.update(entry_record.item_data)

        # Then overlay the structured fields (these take precedence)
        item["id"] = entry_record.frontend_id
        item["item"] = entry_record.item_name

        if entry_record.entry_id:
            item["entryId"] = str(entry_record.entry_id)
        if entry_record.category_id:
            item["categoryId"] = str(entry_record.category_id)

        if entry_record.price is not None:
            item["price"] = float(entry_record.price)
        if entry_record.cost is not None:
            item["cost"] = float(entry_record.cost)
        if entry_record.client_price is not None:
            item["clientPrice"] = float(entry_record.client_price)
        if entry_record.client_cost is not None:
            item["clientCost"] = float(entry_record.client_cost)
        if entry_record.surcharge is not None:
            item["surcharge"] = float(entry_record.surcharge)

        item["taxRate"] = float(entry_record.tax_rate)

        if entry_record.tax_price is not None:
            item["taxPrice"] = float(entry_record.tax_price)

        item["showTax"] = entry_record.show_tax
        item["isLinkedSurcharge"] = entry_record.is_linked_surcharge
        item["marketRange"] = entry_record.market_range

        offers_list.append(item)

    # Reconstruct full details dict from metadata + offers
    details = {}
    if offer.metadata and isinstance(offer.metadata, dict):
        details.update(offer.metadata)
    details["offers"] = offers_list

    return details
