"""Brief-link slug suggestion for vendors.

A cheap LLM proposes a short brandable slug from the vendor branding, with a
deterministic fallback to ``slugify(name)`` and finally ``vendor-<short-uuid>``.
Every candidate is run through the shared validation rules and checked against
existing slugs so the returned value is always free to persist.
"""

from __future__ import annotations

import logging
import uuid

from django.utils.text import slugify

from aivus_backend.core.slugs import SLUG_MAX_LENGTH
from aivus_backend.core.slugs import is_reserved_slug
from aivus_backend.core.slugs import normalize_slug
from aivus_backend.core.slugs import validate_slug

logger = logging.getLogger(__name__)

SLUG_SUGGEST_MODEL = "gemini-2.5-flash-lite"


def _branding_name(vendor_settings) -> str:
    return (
        (vendor_settings.company_name or "").strip()
        or (vendor_settings.agency_name or "").strip()
        or (vendor_settings.vendor.name or "").strip()
        or "vendor"
    )


def _slug_is_taken(candidate: str, *, exclude_vendor_id) -> bool:
    from aivus_backend.users.models import VendorSettings  # noqa: PLC0415

    query = VendorSettings.objects.filter(slug=candidate)
    if exclude_vendor_id is not None:
        query = query.exclude(vendor_id=exclude_vendor_id)
    return query.exists()


def _ensure_available(base: str, *, exclude_vendor_id) -> str:
    """Return a free slug derived from ``base``, appending a numeric suffix on
    collision and falling back to a random tail when truncation would break."""
    base = normalize_slug(base)[:SLUG_MAX_LENGTH].strip("-")
    if not base or validate_slug(base) is not None:
        base = f"vendor-{uuid.uuid4().hex[:8]}"

    if not _slug_is_taken(base, exclude_vendor_id=exclude_vendor_id):
        return base

    for suffix in range(2, 100):
        tail = f"-{suffix}"
        candidate = f"{base[: SLUG_MAX_LENGTH - len(tail)].strip('-')}{tail}"
        if validate_slug(candidate) is None and not _slug_is_taken(
            candidate, exclude_vendor_id=exclude_vendor_id
        ):
            return candidate

    return f"vendor-{uuid.uuid4().hex[:8]}"


def _llm_candidate(name: str) -> str:
    from aivus_backend.core.llm import call_llm  # noqa: PLC0415

    prompt = (
        "Suggest a short, brandable URL slug for a video production agency's "
        "client brief link. Lowercase letters, digits and single hyphens only, "
        "3-30 characters, no spaces. Reply with the slug only, nothing else.\n"
        f"Agency name: {name}"
    )
    response = call_llm(
        model=SLUG_SUGGEST_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=20,
    )
    return normalize_slug(response.content)


def suggest_slug(vendor_settings, *, use_llm: bool = True) -> str:
    """Produce a validated, currently-free slug for the vendor."""
    name = _branding_name(vendor_settings)
    exclude_vendor_id = vendor_settings.vendor_id

    if use_llm:
        try:
            candidate = _llm_candidate(name)
            if candidate and validate_slug(candidate) is None:
                return _ensure_available(candidate, exclude_vendor_id=exclude_vendor_id)
        except Exception:
            logger.warning("slug suggestion LLM failed: vendor=%s", exclude_vendor_id)

    fallback = slugify(name)
    if not fallback or is_reserved_slug(fallback):
        fallback = f"vendor-{uuid.uuid4().hex[:8]}"
    return _ensure_available(fallback, exclude_vendor_id=exclude_vendor_id)
