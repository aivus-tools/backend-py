"""Vendors API serializers (plain Python, no DRF)."""


def serialize_pre_vendor(pre_vendor, request=None):
    """Serialize PreVendor model to dict in camelCase."""
    logo_url = ""
    if pre_vendor.logo:
        logo_url = pre_vendor.logo.url
        if request is not None and not logo_url.startswith(("http://", "https://")):
            logo_url = request.build_absolute_uri(logo_url)

    return {
        "id": str(pre_vendor.id),
        "title": pre_vendor.title,
        "shortDescription": pre_vendor.short_description,
        "portfolioUrl": pre_vendor.portfolio_url,
        "address": pre_vendor.address,
        "email": pre_vendor.email,
        "language": pre_vendor.language,
        "rankLabel": pre_vendor.rank_label,
        "categoryLabel": pre_vendor.category_label,
        "sortOrder": pre_vendor.sort_order,
        "logoUrl": logo_url,
    }
