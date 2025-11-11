from django.db import migrations


CATEGORIES = [
    {"name": "Creative DEVELOPMENT", "level": 1, "parent": None},
    {"name": "PRE-PRODUCTION", "level": 1, "parent": None},
    {"name": "PRODUCTION", "level": 1, "parent": None},
    {"name": "Equipment", "level": 2, "parent": "PRODUCTION"},
    {"name": "Vehicles", "level": 2, "parent": "PRODUCTION"},
    {"name": "Team", "level": 2, "parent": "PRODUCTION"},
]

UNITS = [
    {"key": 1, "name": "Each", "symbol": "ea", "dimension": "QUANTITY"},
    {"key": 2, "name": "Person", "symbol": "pers", "dimension": "QUANTITY"},
    {"key": 3, "name": "Flat", "symbol": "flat", "dimension": "QUANTITY"},
    {"key": 4, "name": "Set", "symbol": "set", "dimension": "QUANTITY"},
    {"key": 5, "name": "Frame(s)", "symbol": "frm", "dimension": "QUANTITY"},
    {"key": 6, "name": "Location", "symbol": "loc", "dimension": "QUANTITY"},
    {"key": 7, "name": "Model", "symbol": "mdl", "dimension": "QUANTITY"},
    {"key": 8, "name": "Pc(s)", "symbol": "pc", "dimension": "QUANTITY"},
    {"key": 9, "name": "Concept", "symbol": "cpt", "dimension": "QUANTITY"},
    {"key": 10, "name": "Hour", "symbol": "h", "dimension": "TEMPORAL"},
    {"key": 11, "name": "Day", "symbol": "d", "dimension": "TEMPORAL"},
    {"key": 12, "name": "Sec", "symbol": "s", "dimension": "TEMPORAL"},
    {"key": 13, "name": "Min", "symbol": "min", "dimension": "TEMPORAL"},
]

ENTRIES = [
    # Creative DEVELOPMENT
    {"name": "Concept Development", "category": "Creative DEVELOPMENT", "units": [9, 3]},
    {"name": "KV Development", "category": "Creative DEVELOPMENT", "units": [1, 3]},
    {"name": "Scriptwriting", "category": "Creative DEVELOPMENT", "units": [1, 3, 13]},
    {"name": "Storyboard", "category": "Creative DEVELOPMENT", "units": [5, 3, 13]},
    {"name": "Animatic", "category": "Creative DEVELOPMENT", "units": [1, 3, 12, 13]},
    # PRE-PRODUCTION
    {"name": "CAST TALENT", "category": "PRE-PRODUCTION", "units": [7, 3]},
    {"name": "SCOUT LOCATIONS", "category": "PRE-PRODUCTION", "units": [6, 3, 11]},
    {"name": "GEAR PREP DAY", "category": "PRE-PRODUCTION", "units": [3, 11]},
    {"name": "Director's Treatment", "category": "PRE-PRODUCTION", "units": [1, 3]},
    # PRODUCTION - Equipment
    {"name": "Camera", "category": "Equipment", "units": [4, 8, 11]},
    {"name": "Lenses", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Monitors", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Additional Camera Accessories", "category": "Equipment", "units": [4, 11]},
    {"name": "Drones", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Dolly", "category": "Equipment", "units": [4, 11]},
    {"name": "Cranes & Jibs", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Stabilizers and Gimbals", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Sliders", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Motion control system", "category": "Equipment", "units": [4, 11]},
    {"name": "Lighting", "category": "Equipment", "units": [4, 11]},
    {"name": "Electric Generators", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Sound Recording Equipment", "category": "Equipment", "units": [4, 11]},
    {"name": "Teleprompter", "category": "Equipment", "units": [4, 1, 11]},
    {"name": "Walkie Talkie", "category": "Equipment", "units": [4, 1, 11]},
    # PRODUCTION - Vehicles
    {"name": "Camera Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Grip/Lighting Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Makeup Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Wardrobe Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Talent Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Catering Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Production Office Trailer", "category": "Vehicles", "units": [1, 11]},
    {"name": "Tech Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Props Truck", "category": "Vehicles", "units": [1, 11]},
    {"name": "Portable Toilet", "category": "Vehicles", "units": [1, 11]},
    # PRODUCTION - Team
    {"name": "Creative Director", "category": "Team", "units": [2, 10, 11]},
    {"name": "Art Director", "category": "Team", "units": [2, 10, 11]},
    {"name": "Director", "category": "Team", "units": [2, 10, 11]},
    {"name": "Director's Assistant", "category": "Team", "units": [2, 10, 11]},
    {"name": "On-Set Editor", "category": "Team", "units": [2, 10, 11]},
    {"name": "DP / Cinematographer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Focus puller", "category": "Team", "units": [2, 10, 11]},
    {"name": "Camera Assistant (1st AC)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Extra Camera Operator", "category": "Team", "units": [2, 10, 11]},
    {"name": "Camera Tech", "category": "Team", "units": [2, 10, 11]},
    {"name": "Aerial Cinematographer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Gaffer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Grip", "category": "Team", "units": [2, 10, 11]},
    {"name": "Field/Audio Recorder", "category": "Team", "units": [2, 10, 11]},
    {"name": "Photographer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Producer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Production Assistant", "category": "Team", "units": [2, 10, 11]},
    {"name": "Production Designer", "category": "Team", "units": [2, 10, 11]},
    {"name": "Production Assistant(s)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Set Decorator", "category": "Team", "units": [2, 10, 11]},
    {"name": "Assistant Set Decorator(s)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Property Master", "category": "Team", "units": [2, 10, 11]},
    {"name": "Assistant Property Master", "category": "Team", "units": [2, 10, 11]},
    {"name": "On-Set Props Assistant(s)", "category": "Team", "units": [2, 10, 11]},
    {"name": "Worker(s)", "category": "Team", "units": [2, 10, 11]},
]


def seed_catalog(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Unit = apps.get_model("catalog", "Unit")
    Entry = apps.get_model("catalog", "Entry")
    EntryUnit = apps.get_model("catalog", "EntryUnit")

    if Entry.objects.exists():
        # Catalog already seeded
        return

    category_cache = {}
    for category_data in CATEGORIES:
        parent = category_cache.get(category_data["parent"])
        obj, _ = Category.objects.get_or_create(
            name=category_data["name"],
            defaults={
                "level": category_data["level"],
                "parent_category": parent,
            },
        )
        if obj.parent_category_id != (parent.id if parent else None) or obj.level != category_data["level"]:
            obj.parent_category = parent
            obj.level = category_data["level"]
            obj.save(update_fields=["parent_category", "level"])

        category_cache[category_data["name"]] = obj

    unit_cache = {}
    for unit_data in UNITS:
        obj, _ = Unit.objects.get_or_create(
            name=unit_data["name"],
            defaults={
                "symbol": unit_data["symbol"],
                "dimension": unit_data["dimension"],
            },
        )
        if obj.symbol != unit_data["symbol"] or obj.dimension != unit_data["dimension"]:
            obj.symbol = unit_data["symbol"]
            obj.dimension = unit_data["dimension"]
            obj.save(update_fields=["symbol", "dimension"])

        unit_cache[unit_data["key"]] = obj

    for entry_data in ENTRIES:
        category = category_cache[entry_data["category"]]
        entry, created = Entry.objects.get_or_create(
            name=entry_data["name"],
            defaults={
                "category": category,
                "description": "",
                "is_approved": True,
            },
        )
        if not created and entry.category_id != category.id:
            entry.category = category
            entry.save(update_fields=["category"])

        if not entry.is_approved or entry.description:
            entry.is_approved = True
            entry.description = ""
            entry.save(update_fields=["is_approved", "description"])

        for index, unit_key in enumerate(entry_data["units"]):
            unit = unit_cache[unit_key]
            EntryUnit.objects.get_or_create(
                entry=entry,
                unit=unit,
                defaults={
                    "is_default": index == 0,
                },
            )


def unseed_catalog(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Unit = apps.get_model("catalog", "Unit")
    Entry = apps.get_model("catalog", "Entry")
    EntryUnit = apps.get_model("catalog", "EntryUnit")

    entry_names = [entry["name"] for entry in ENTRIES]
    EntryUnit.objects.filter(entry__name__in=entry_names).delete()
    Entry.objects.filter(name__in=entry_names).delete()

    unit_names = [unit["name"] for unit in UNITS]
    Unit.objects.filter(name__in=unit_names).delete()

    category_names = [category["name"] for category in CATEGORIES]
    Category.objects.filter(name__in=category_names).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_alter_entry_description"),
    ]

    operations = [
        migrations.RunPython(seed_catalog, reverse_code=unseed_catalog),
    ]

