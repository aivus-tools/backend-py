"""Catalog API serializers (plain Python, no DRF)."""


def serialize_category(category):
    """Serialize Category model to dict."""
    return {
        "id": str(category.id),
        "name": category.name,
        "level": category.level,
        "parentCategoryId": str(category.parent_category_id) if category.parent_category_id else None,
    }


def serialize_entry_unit(entry_unit):
    """Serialize EntryUnit with unit details."""
    return {
        "id": str(entry_unit.unit.id),
        "name": entry_unit.unit.name,
        "symbol": entry_unit.unit.symbol,
        "dimension": entry_unit.unit.dimension,
        "isDefault": entry_unit.is_default,
    }


def serialize_entry(entry, include_units=True):
    """Serialize Entry model to dict."""
    data = {
        "id": str(entry.id),
        "name": entry.name,
        "categoryId": str(entry.category_id),
    }

    if include_units:
        data["description"] = entry.description
        # Get units grouped by dimension
        entry_units = entry.entry_units.select_related("unit").all()

        quantity_units = []
        temporal_units = []

        for entry_unit in entry_units:
            unit_data = serialize_entry_unit(entry_unit)
            if entry_unit.unit.dimension == "QUANTITY":
                quantity_units.append(unit_data)
            elif entry_unit.unit.dimension == "TEMPORAL":
                temporal_units.append(unit_data)

        data["units"] = {
            "quantity": quantity_units,
            "temporal": temporal_units,
        }

    return data

