import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.oauth import (
    OAuthProviderConfig,
    delete_oauth_provider,
    get_oauth_provider,
    list_oauth_providers,
    upsert_oauth_provider,
)
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_get_unknown_slug_returns_none(db: Session):
    assert get_oauth_provider(db, "google") is None


def test_list_empty(db: Session):
    assert list_oauth_providers(db) == []


def test_upsert_and_get_roundtrip(db: Session):
    provider = OAuthProviderConfig(
        slug="google",
        name="Google",
        enabled=True,
        client_id="cid",
        client_secret="csec",
        issuer_url="",
    )
    upsert_oauth_provider(db, provider)
    assert get_oauth_provider(db, "google") == provider


def test_upsert_overwrites(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=False,
                            client_id="old", client_secret="old", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=True,
                            client_id="new", client_secret="new", issuer_url=""),
    )
    got = get_oauth_provider(db, "google")
    assert got is not None
    assert got.enabled is True
    assert got.client_id == "new"


def test_list_returns_all_slugs(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=True,
                            client_id="a", client_secret="b", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="pocketid", name="PocketID", enabled=True,
                            client_id="c", client_secret="d",
                            issuer_url="https://id.example.org"),
    )
    slugs = sorted(p.slug for p in list_oauth_providers(db))
    assert slugs == ["google", "pocketid"]


def test_list_returns_sorted_by_slug(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="zeta", name="Z", enabled=True,
                            client_id="a", client_secret="b", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="alpha", name="A", enabled=True,
                            client_id="c", client_secret="d", issuer_url=""),
    )
    assert [p.slug for p in list_oauth_providers(db)] == ["alpha", "zeta"]


def test_partial_rows_surface_in_list(db: Session):
    # A provider with only a `name` row but no `client_id` is incomplete.
    # list_oauth_providers should still surface it (with empty fields) so
    # the operator can finish the configuration; downstream code is
    # responsible for treating empty credentials as disabled.
    db.add(AppConfig(key="oauth.partial.name", value="Partial"))
    db.commit()
    providers = list_oauth_providers(db)
    assert len(providers) == 1
    assert providers[0].slug == "partial"
    assert providers[0].name == "Partial"
    assert providers[0].client_id == ""


def test_delete_removes_all_rows_for_slug(db: Session):
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="google", name="Google", enabled=True,
                            client_id="a", client_secret="b", issuer_url=""),
    )
    upsert_oauth_provider(
        db,
        OAuthProviderConfig(slug="pocketid", name="PocketID", enabled=True,
                            client_id="c", client_secret="d",
                            issuer_url="https://id.example.org"),
    )
    delete_oauth_provider(db, "google")
    assert get_oauth_provider(db, "google") is None
    assert get_oauth_provider(db, "pocketid") is not None
    # And no orphan google rows:
    leftover = (
        db.query(AppConfig)
        .filter(AppConfig.key.like("oauth.google.%"))
        .all()
    )
    assert leftover == []


def test_enabled_parses_truthy(db: Session):
    db.add(AppConfig(key="oauth.x.name", value="X"))
    db.add(AppConfig(key="oauth.x.enabled", value="true"))
    db.add(AppConfig(key="oauth.x.client_id", value="cid"))
    db.add(AppConfig(key="oauth.x.client_secret", value="csec"))
    db.add(AppConfig(key="oauth.x.issuer_url", value=""))
    db.commit()
    got = get_oauth_provider(db, "x")
    assert got is not None and got.enabled is True


def test_enabled_defaults_to_false(db: Session):
    db.add(AppConfig(key="oauth.x.name", value="X"))
    db.add(AppConfig(key="oauth.x.client_id", value="cid"))
    db.commit()
    got = get_oauth_provider(db, "x")
    assert got is not None and got.enabled is False
