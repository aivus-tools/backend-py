"""Vendor brief-link slug validation and reserved-name registry.

Single source of truth for slug rules used by vendor settings, the public
by-slug resolver and the slug-suggestion helper. Reserved names combine the
real top-level segments of the frontend app router with service keywords so a
vendor link can never shadow an existing route.
"""

from __future__ import annotations

import re

SLUG_MIN_LENGTH = 3
SLUG_MAX_LENGTH = 40

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        # Frontend top-level app-router segments.
        "app",
        "api",
        "auth",
        "export",
        "external",
        "public",
        "public-brief",
        "shared-brief",
        # Service and reserved keywords.
        "admin",
        "brief",
        "service",
        "settings",
        "vendor",
        "client",
        "www",
        "go",
        "embed",
        "static",
        "assets",
        "media",
        "success",
        "login",
        "logout",
        "register",
        "signup",
        "signin",
        "account",
        "dashboard",
        "help",
        "support",
        "about",
        "terms",
        "privacy",
    }
)


def normalize_slug(value: str) -> str:
    return (value or "").strip().lower()


def is_reserved_slug(value: str) -> bool:
    return normalize_slug(value) in RESERVED_SLUGS


def validate_slug(value: str) -> str | None:
    """Return an error message when the slug is invalid, otherwise None.

    Enforces lowercase ``[a-z0-9-]`` with no leading, trailing or doubled
    hyphens, a 3-40 length window and the reserved-name registry. Callers pass
    the value through ``normalize_slug`` first, so input is already lowercased
    and trimmed by the time it is validated.
    """
    slug = (value or "").strip()
    if not slug:
        return "Slug is required"
    if len(slug) < SLUG_MIN_LENGTH or len(slug) > SLUG_MAX_LENGTH:
        return f"Slug must be {SLUG_MIN_LENGTH}-{SLUG_MAX_LENGTH} characters"
    if not SLUG_PATTERN.match(slug):
        return "Slug may contain only lowercase letters, digits and single hyphens"
    if is_reserved_slug(slug):
        return "This link is reserved"
    return None
