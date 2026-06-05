"""Shared OIDC slug validation + env-middle conversion.

Used by both the backend Settings validator and the setup wizard so the
rules can't drift between them.
"""

from __future__ import annotations

import re

RESERVED_SLUGS: frozenset[str] = frozenset({
    "google", "github", "microsoft", "discord", "facebook", "oidc",
})

_SLUG_OK = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")


def slugify(name: str) -> str:
    """Convert a friendly name into a URL slug. Non-alphanumeric runs become dashes."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


def validate_slug(slug: str) -> str | None:
    """Return None if slug is valid, else a human-readable error message."""
    if not _SLUG_OK.match(slug):
        return (
            "must be lowercase letters, digits, and single dashes between groups "
            "(no leading/trailing dash, no consecutive dashes)"
        )
    if slug in RESERVED_SLUGS:
        return f"'{slug}' is reserved; pick a different slug"
    return None


def slug_from_env_middle(middle: str) -> str:
    """Convert the captured middle of an env var name to a URL slug."""
    return middle.lower().replace("_", "-")


def env_middle_from_slug(slug: str) -> str:
    """Inverse of slug_from_env_middle — used when writing env vars."""
    return slug.upper().replace("-", "_")
