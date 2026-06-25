"""Net CRUD service.

Provides create_net, list_nets, update_net, delete_net, add_member,
remove_member, list_memberships, list_net_config.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.modules.nets.models import Net, NetConfig, NetMembership, NetRole

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))*$")


def validate_slug(slug: str) -> None:
    if not (1 <= len(slug) <= 64) or not _SLUG_RE.match(slug):
        raise ValueError("Slug must be 1-64 chars, lowercase alphanumerics, no consecutive or edge hyphens")


# ---------------------------------------------------------------------------
# Net CRUD
# ---------------------------------------------------------------------------


def create_net(db: Session, *, slug: str, name: str, creator_callsign: str) -> Net:
    """Create a new Net, validate slug, seed default templates, return the Net."""
    validate_slug(slug)

    existing = db.query(Net).filter(Net.slug == slug).one_or_none()
    if existing is not None:
        raise ValueError(f"A net with slug '{slug}' already exists")

    net = Net(slug=slug, name=name)
    db.add(net)
    db.flush()  # assign net.id

    # Seed default net content (roster + reminder templates).
    # TODO (Tasks 8/9): once RosterTemplate and ReminderTemplate models gain
    # a net_id FK column, call seeds.seed_default_net_content(db, net.id) here.
    # For now we skip template seeding; the Default Net templates were backfilled
    # by the multi_net_cutover migration, and new nets will receive templates
    # once those modules are made net-aware.

    db.commit()
    db.refresh(net)
    return net


def list_nets(db: Session, *, user: User) -> list[Net]:
    """Return all nets for admins; nets the user has membership in for others."""
    if user.is_admin:
        return db.query(Net).all()
    memberships = db.query(NetMembership).filter(NetMembership.user_callsign == user.callsign).all()
    net_ids = [m.net_id for m in memberships]
    if not net_ids:
        return []
    return db.query(Net).filter(Net.id.in_(net_ids)).all()


def update_net(
    db: Session,
    *,
    net: Net,
    slug: str | None = None,
    name: str | None = None,
    is_public: bool | None = None,
) -> Net:
    """Update mutable Net fields. Returns the updated Net."""
    if slug is not None:
        validate_slug(slug)
        existing = db.query(Net).filter(Net.slug == slug, Net.id != net.id).one_or_none()
        if existing is not None:
            raise ValueError(f"A net with slug '{slug}' already exists")
        net.slug = slug
    if name is not None:
        net.name = name
    if is_public is not None:
        net.is_public = is_public
    db.commit()
    db.refresh(net)
    return net


def delete_net(db: Session, *, net: Net) -> None:
    """Delete a Net and all its per-net data (cascade via ORM relationship)."""
    # NetMembership and NetConfig cascade via relationship(cascade="all, delete-orphan")
    # on Net.memberships. NetConfig has FK to nets.id without ORM relationship on Net,
    # so we delete those manually before deleting the net.
    db.query(NetConfig).filter(NetConfig.net_id == net.id).delete()
    db.delete(net)
    db.commit()


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def add_member(db: Session, *, net: Net, callsign: str, role: NetRole) -> NetMembership:
    """Add or update a user's membership in a net. Bumps token_version."""
    user = db.get(User, callsign)
    if user is None:
        raise ValueError(f"User '{callsign}' not found")

    existing = db.get(NetMembership, (callsign, net.id))
    if existing is not None:
        existing.role = role
        m = existing
    else:
        m = NetMembership(user_callsign=callsign, net_id=net.id, role=role)
        db.add(m)

    # Bump token_version to invalidate outstanding JWTs for this user.
    user.token_version += 1
    db.commit()
    db.refresh(m)
    return m


def remove_member(db: Session, *, net: Net, callsign: str) -> None:
    """Remove a user from a net. Bumps token_version. Raises ValueError if not found."""
    m = db.get(NetMembership, (callsign, net.id))
    if m is None:
        raise ValueError(f"User '{callsign}' is not a member of net '{net.slug}'")

    user = db.get(User, callsign)
    if user is not None:
        user.token_version += 1

    db.delete(m)
    db.commit()


def list_memberships(db: Session, *, net: Net) -> list[dict]:
    """Return memberships for a net as dicts with callsign, name, role."""
    rows = (
        db.query(NetMembership, User)
        .join(User, User.callsign == NetMembership.user_callsign)
        .filter(NetMembership.net_id == net.id)
        .all()
    )
    return [{"callsign": m.user_callsign, "name": u.name, "role": m.role} for m, u in rows]


# ---------------------------------------------------------------------------
# Per-net config
# ---------------------------------------------------------------------------


def list_net_config(db: Session, *, net: Net) -> dict[str, str]:
    """Return all config key→value pairs for a net."""
    rows = db.query(NetConfig).filter(NetConfig.net_id == net.id).all()
    return {row.key: row.value for row in rows}
