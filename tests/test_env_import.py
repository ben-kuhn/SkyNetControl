import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config_mgmt.env_import import import_env_to_app_config
from backend.config_mgmt.models import AppConfig
from backend.config_mgmt.oauth import get_oauth_provider, list_oauth_providers
from backend.config_mgmt.setup_state import is_setup_completed
from backend.config_mgmt.smtp import get_smtp_config
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


def test_empty_env_with_empty_db_does_not_mark_setup_complete(db: Session):
    import_env_to_app_config(db, {})
    assert is_setup_completed(db) is False


def test_empty_env_with_existing_db_rows_marks_setup_complete(db: Session):
    # Pretend the user already populated net_address via the (future)
    # Config page — we shouldn't relaunch the wizard on next boot.
    db.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
    db.commit()
    import_env_to_app_config(db, {})
    assert is_setup_completed(db) is True


def test_imports_fixed_provider_from_env(db: Session):
    env = {
        "SKYNET_AUTH_GOOGLE__ENABLED": "true",
        "SKYNET_AUTH_GOOGLE__CLIENT_ID": "google-cid",
        "SKYNET_AUTH_GOOGLE__CLIENT_SECRET": "google-csec",
    }
    import_env_to_app_config(db, env)
    google = get_oauth_provider(db, "google")
    assert google is not None
    assert google.enabled is True
    assert google.client_id == "google-cid"
    assert google.client_secret == "google-csec"
    assert google.name == "Google"
    assert google.issuer_url == ""
    assert is_setup_completed(db) is True


def test_imports_oidc_provider_from_env(db: Session):
    env = {
        "SKYNET_AUTH_OIDC_POCKETID_NAME": "PocketID",
        "SKYNET_AUTH_OIDC_POCKETID_ENABLED": "true",
        "SKYNET_AUTH_OIDC_POCKETID_CLIENT_ID": "pocket-cid",
        "SKYNET_AUTH_OIDC_POCKETID_CLIENT_SECRET": "pocket-csec",
        "SKYNET_AUTH_OIDC_POCKETID_ISSUER_URL": "https://id.example.org",
    }
    import_env_to_app_config(db, env)
    pocket = get_oauth_provider(db, "pocketid")
    assert pocket is not None
    assert pocket.name == "PocketID"
    assert pocket.issuer_url == "https://id.example.org"


def test_imports_smtp_from_env(db: Session):
    env = {
        "SKYNET_SMTP__HOST": "smtp.example.org",
        "SKYNET_SMTP__PORT": "587",
        "SKYNET_SMTP__USERNAME": "user",
        "SKYNET_SMTP__PASSWORD": "pass",
        "SKYNET_SMTP__FROM_ADDRESS": "net@example.org",
        "SKYNET_SMTP__USE_TLS": "true",
    }
    import_env_to_app_config(db, env)
    smtp = get_smtp_config(db)
    assert smtp is not None
    assert smtp.host == "smtp.example.org"
    assert smtp.port == 587
    assert smtp.use_tls is True


def test_idempotent_on_second_run(db: Session):
    env = {
        "SKYNET_AUTH_GOOGLE__ENABLED": "true",
        "SKYNET_AUTH_GOOGLE__CLIENT_ID": "google-cid",
        "SKYNET_AUTH_GOOGLE__CLIENT_SECRET": "google-csec",
    }
    import_env_to_app_config(db, env)
    # Mutate the row manually to simulate an admin edit, then re-run
    # the import; the admin's value must NOT be overwritten.
    from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
    upsert_oauth_provider(db, OAuthProviderConfig(
        slug="google", name="Google", enabled=True,
        client_id="admin-edited-cid", client_secret="admin-edited-csec",
        issuer_url="",
    ))
    import_env_to_app_config(db, env)
    google = get_oauth_provider(db, "google")
    assert google is not None
    assert google.client_id == "admin-edited-cid"  # not the env value


def test_skips_disabled_fixed_provider_with_no_credentials(db: Session):
    env = {
        "SKYNET_AUTH_GOOGLE__ENABLED": "false",
        # No client_id / client_secret
    }
    import_env_to_app_config(db, env)
    # No oauth.* rows written; nothing else to do.
    rows = list_oauth_providers(db)
    assert rows == []


def test_invalid_oidc_slug_is_skipped(db: Session):
    # If someone set SKYNET_AUTH_OIDC_GOOGLE_* (slug "google" is reserved),
    # the migration should skip it rather than crash.
    env = {
        "SKYNET_AUTH_OIDC_GOOGLE_NAME": "Custom Google",
        "SKYNET_AUTH_OIDC_GOOGLE_ENABLED": "true",
        "SKYNET_AUTH_OIDC_GOOGLE_CLIENT_ID": "x",
        "SKYNET_AUTH_OIDC_GOOGLE_CLIENT_SECRET": "y",
        "SKYNET_AUTH_OIDC_GOOGLE_ISSUER_URL": "https://example.org",
    }
    import_env_to_app_config(db, env)
    # Reserved slug skipped:
    assert get_oauth_provider(db, "google") is None
