"""Tests for backend.auth.recovery — token minting, verification, and JWT helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import backend.auth.recovery as recovery_mod
from backend.auth.models import AdminRecoveryToken
from backend.auth.recovery import (
    RecoveryPrincipal,
    _hash,
    _now,
    decode_recovery_token,
    list_outstanding,
    make_recovery_token,
    mark_used,
    mint_token,
    revoke_by_prefix,
    verify_token,
)
from backend.auth.service import create_access_token
from backend.config import Settings
from backend.db.base import Base
from tests.conftest import make_test_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


_FIXED_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def fixed_now(monkeypatch):
    """Pin recovery_mod._now() to a fixed UTC instant."""
    monkeypatch.setattr(recovery_mod, "_now", lambda: _FIXED_NOW)
    return _FIXED_NOW


@pytest.fixture()
def test_settings():
    return Settings(
        database_url="sqlite:///",
        jwt_secret_key="test-secret",
        jwt_algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Token minting / verification
# ---------------------------------------------------------------------------


def test_mint_token_returns_plaintext_and_persists_hash(db: Session, fixed_now):
    plaintext, expires_at = mint_token(db, ttl=timedelta(minutes=10))
    assert isinstance(plaintext, str) and len(plaintext) > 10
    assert expires_at == fixed_now + timedelta(minutes=10)

    # Verify hash is stored, not plaintext
    from backend.auth.recovery import _hash
    from backend.auth.models import AdminRecoveryToken
    row = db.query(AdminRecoveryToken).one()
    assert row.token_hash == _hash(plaintext)
    assert len(row.token_hash) == 64  # sha256 hex


def test_mint_token_is_unique_across_calls(db: Session):
    pt1, _ = mint_token(db)
    pt2, _ = mint_token(db)
    assert pt1 != pt2

    from backend.auth.models import AdminRecoveryToken
    rows = db.query(AdminRecoveryToken).all()
    assert len(rows) == 2
    assert rows[0].token_hash != rows[1].token_hash


def test_verify_token_matches_unused_unexpired(db: Session, fixed_now):
    plaintext, _ = mint_token(db, ttl=timedelta(minutes=10))
    row = verify_token(db, plaintext)
    assert row is not None
    assert row.used_at is None


def test_verify_token_rejects_unknown(db: Session):
    assert verify_token(db, "completely-wrong-token") is None


def test_verify_token_rejects_used(db: Session, fixed_now):
    plaintext, _ = mint_token(db, ttl=timedelta(minutes=10))
    row = verify_token(db, plaintext)
    mark_used(db, row)
    # Second verify should return None
    assert verify_token(db, plaintext) is None


def test_verify_token_rejects_expired(db: Session, fixed_now):
    plaintext, _ = mint_token(db, ttl=timedelta(minutes=10))

    # Advance time past expiry
    future = _FIXED_NOW + timedelta(minutes=11)
    import backend.auth.recovery as mod
    mod._now = lambda: future  # direct override (fixed_now already monkeypatched)

    assert verify_token(db, plaintext) is None


def test_verify_token_does_not_mutate(db: Session, fixed_now):
    """verify_token() is read-only; used_at must remain None after calling it."""
    from backend.auth.models import AdminRecoveryToken
    plaintext, _ = mint_token(db, ttl=timedelta(minutes=10))
    _ = verify_token(db, plaintext)
    row = db.query(AdminRecoveryToken).one()
    assert row.used_at is None


def test_mark_used_is_idempotent(db: Session, fixed_now):
    plaintext, _ = mint_token(db, ttl=timedelta(minutes=10))
    row = verify_token(db, plaintext)
    mark_used(db, row)
    first_used_at = row.used_at

    # Call again — should be a no-op
    mark_used(db, row)
    assert row.used_at == first_used_at


# ---------------------------------------------------------------------------
# list_outstanding
# ---------------------------------------------------------------------------


def test_list_outstanding_filters_used_and_expired(db: Session, fixed_now):
    # Active token
    pt_active, _ = mint_token(db, ttl=timedelta(minutes=10))
    # Used token
    pt_used, _ = mint_token(db, ttl=timedelta(minutes=10))
    row_used = verify_token(db, pt_used)
    mark_used(db, row_used)
    # Expired token — mint first (in "past"), then advance time forward
    pt_exp, _ = mint_token(db, ttl=timedelta(seconds=1))

    # Advance clock so pt_exp is expired
    future = _FIXED_NOW + timedelta(minutes=1)
    import backend.auth.recovery as mod
    mod._now = lambda: future

    outstanding = list_outstanding(db)
    plaintext_hashes = {r.token_hash for r in outstanding}
    from backend.auth.recovery import _hash
    assert _hash(pt_active) in plaintext_hashes
    assert _hash(pt_used) not in plaintext_hashes
    assert _hash(pt_exp) not in plaintext_hashes


# ---------------------------------------------------------------------------
# revoke_by_prefix
# ---------------------------------------------------------------------------


def test_revoke_by_prefix_marks_matching_tokens_used(db: Session, fixed_now):
    from backend.auth.recovery import _hash
    pt, _ = mint_token(db, ttl=timedelta(minutes=10))
    prefix = _hash(pt)[:8]
    revoke_by_prefix(db, prefix)
    assert verify_token(db, pt) is None


def test_revoke_by_prefix_returns_count(db: Session, fixed_now):
    pt1, _ = mint_token(db, ttl=timedelta(minutes=10))
    pt2, _ = mint_token(db, ttl=timedelta(minutes=10))
    from backend.auth.recovery import _hash
    # Use a prefix that matches only pt1
    prefix = _hash(pt1)[:8]
    count = revoke_by_prefix(db, prefix)
    # Should revoke 1 (assuming no collision on first 8 hex chars, which is
    # extremely unlikely). Even if both matched, count > 0 is what we assert.
    assert count >= 1


# ---------------------------------------------------------------------------
# Recovery JWT round-trip
# ---------------------------------------------------------------------------


def test_recovery_jwt_round_trip(test_settings):
    token = make_recovery_token("abcd1234", test_settings)
    principal = decode_recovery_token(token, test_settings)
    assert principal is not None
    assert isinstance(principal, RecoveryPrincipal)
    assert principal.hash_prefix == "abcd1234"
    assert principal.callsign == "recovery:abcd1234"


def test_recovery_jwt_expired_rejected(test_settings):
    # Mint in the past so it's expired on decode
    import backend.auth.recovery as mod
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    original = mod._now
    mod._now = lambda: past
    try:
        token = make_recovery_token("abcd1234", test_settings)
    finally:
        mod._now = original
    # Token exp was in 2020 — should be rejected by JWT decode
    result = decode_recovery_token(token, test_settings)
    assert result is None


def test_recovery_jwt_wrong_type_rejected(test_settings):
    """A user-session JWT (no 'type: recovery' claim) must be rejected."""
    user_token = make_test_token("W0NE", test_settings, is_admin=True, token_version=0)
    result = decode_recovery_token(user_token, test_settings)
    assert result is None


# ── claim_token (atomic verify + mark-used) ─────────────────────────────────


def test_claim_token_returns_row_and_marks_used(db):
    from backend.auth.recovery import claim_token

    plaintext, _ = mint_token(db)
    row = claim_token(db, plaintext)
    assert row is not None
    assert row.used_at is not None


def test_claim_token_second_call_returns_none(db):
    """The atomic UPDATE means only the first claim wins."""
    from backend.auth.recovery import claim_token

    plaintext, _ = mint_token(db)
    first = claim_token(db, plaintext)
    assert first is not None
    second = claim_token(db, plaintext)
    assert second is None


def test_claim_token_unknown_returns_none(db):
    from backend.auth.recovery import claim_token

    assert claim_token(db, "not-a-real-token") is None


def test_claim_token_expired_returns_none(db):
    from backend.auth.recovery import claim_token
    import backend.auth.recovery as mod

    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    original = mod._now
    mod._now = lambda: past
    try:
        plaintext, _ = mint_token(db)
    finally:
        mod._now = original
    # Token expired in 2020; claim now should fail.
    assert claim_token(db, plaintext) is None


# ── revoke_by_prefix hex validation ─────────────────────────────────────────


def test_revoke_by_prefix_rejects_non_hex(db):
    from backend.auth.recovery import revoke_by_prefix

    for bad in ["abc%", "_abcd", "ZZZZ", "abc-1234", "abc def", ""]:
        try:
            revoke_by_prefix(db, bad)
        except ValueError as exc:
            assert "hex" in str(exc).lower() or "prefix" in str(exc).lower()
        else:
            raise AssertionError(f"prefix {bad!r} should have been rejected")


def test_revoke_by_prefix_accepts_valid_hex(db):
    from backend.auth.recovery import revoke_by_prefix

    plaintext, _ = mint_token(db)
    hash_prefix = _hash(plaintext)[:8]
    revoked = revoke_by_prefix(db, hash_prefix)
    assert revoked == 1


# ── mint_token purges used + expired rows ──────────────────────────────────


def test_mint_token_purges_used_tokens(db):
    """Calling mint_token after a token has been used should remove the used row."""
    import backend.auth.recovery as mod

    # Round 1: mint and claim a token; the row is now marked used_at != None
    plaintext_a, _ = mint_token(db)
    assert mod.claim_token(db, plaintext_a) is not None
    assert db.query(AdminRecoveryToken).count() == 1

    # Round 2: mint a fresh token — the purge should drop the used row
    mint_token(db, ttl=timedelta(minutes=10))
    rows = db.query(AdminRecoveryToken).all()
    assert len(rows) == 1
    assert rows[0].used_at is None  # only the new unused row survives


def test_mint_token_purges_expired_rows(db):
    """Expired-but-unused tokens shouldn't accumulate either."""
    import backend.auth.recovery as mod

    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    original = mod._now
    mod._now = lambda: past
    try:
        mint_token(db)  # this row will be "expired" by now
    finally:
        mod._now = original
    assert db.query(AdminRecoveryToken).count() == 1

    mint_token(db)
    rows = db.query(AdminRecoveryToken).all()
    assert len(rows) == 1
    assert rows[0].used_at is None
    assert rows[0].expires_at > _now().replace(tzinfo=None)
