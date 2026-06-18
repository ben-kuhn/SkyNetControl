"""Tests for the AEAD envelope used to protect AppConfig secrets at rest."""
import os

from backend.auth.secret_box import _PREFIX, decrypt, encrypt, install_key_material


def test_create_app_prefers_secrets_key_over_jwt_secret(monkeypatch, tmp_path):
    """install_key_material is called with settings.secrets_key when set,
    otherwise jwt_secret_key. Rotating the JWT signing key without
    SKYNET_SECRETS_KEY should NOT invalidate encrypted rows.
    """
    monkeypatch.setenv("SKYNET_JWT_SECRET_KEY", "jwt-key-A")
    monkeypatch.setenv("SKYNET_SECRETS_KEY", "secrets-key-A")
    monkeypatch.setenv("SKYNET_DATABASE_URL", f"sqlite:///{tmp_path}/a.db")

    # Reload Settings so the test envvars take effect.
    from backend.config import Settings

    from backend.app import create_app

    s = Settings()
    assert s.secrets_key == "secrets-key-A"
    create_app(settings=s)
    # The box was bound to secrets-key-A; ciphertext from this point is
    # decryptable with secrets-key-A.
    ct = encrypt("hello")
    # Now rotate ONLY the JWT secret and rebuild Settings — secrets_key stays.
    monkeypatch.setenv("SKYNET_JWT_SECRET_KEY", "jwt-key-B")
    s2 = Settings()
    create_app(settings=s2)
    # Ciphertext from the previous rotation still decrypts.
    assert decrypt(ct) == "hello"

    # Cleanup any env leaks.
    for k in ("SKYNET_JWT_SECRET_KEY", "SKYNET_SECRETS_KEY", "SKYNET_DATABASE_URL"):
        os.environ.pop(k, None)
    install_key_material("test-secret")


def test_round_trip():
    install_key_material("test-secret")
    ct = encrypt("real-google-secret")
    assert ct.startswith(_PREFIX)
    assert "real-google-secret" not in ct  # ciphertext doesn't leak the plaintext
    assert decrypt(ct) == "real-google-secret"


def test_empty_string_passes_through():
    """Empty input is the "no value" sentinel; encrypt/decrypt return "" unchanged.

    This keeps the no-secret case (disabled OAuth provider, SMTP without auth)
    from rendering as a useless 28-byte envelope of nothing.
    """
    install_key_material("test-secret")
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_legacy_plaintext_passthrough_on_decrypt():
    """Rows written before secret_box landed are stored as raw plaintext.

    decrypt() must return them unchanged so existing installs keep
    working until the next admin save re-encrypts. Anything without the
    `enc:v1:` prefix is treated as legacy plaintext.
    """
    install_key_material("test-secret")
    assert decrypt("legacy-plain-secret") == "legacy-plain-secret"


def test_each_ciphertext_uses_a_fresh_nonce():
    """Two encryptions of the same plaintext must produce distinct ciphertexts.

    Otherwise an attacker reading the DB could tell which providers share
    a client_secret value (and confirm guesses by reproducing the
    ciphertext of a known plaintext).
    """
    install_key_material("test-secret")
    a = encrypt("same-plaintext")
    b = encrypt("same-plaintext")
    assert a != b
    assert decrypt(a) == decrypt(b) == "same-plaintext"


def test_key_change_breaks_decrypt():
    """If the operator rotates the JWT secret without re-encrypting, the
    encrypted rows become unrecoverable. Document this with a test so the
    behaviour is intentional, not surprising. The wizard's preserve-on-
    empty sentinel is the recovery path: operator just re-enters secrets."""
    install_key_material("key-A")
    ct = encrypt("hello")

    install_key_material("key-B")
    # Different key derived from "key-B" cannot decrypt rows encrypted
    # under "key-A". The cryptography library raises InvalidTag.
    from cryptography.exceptions import InvalidTag
    try:
        decrypt(ct)
    except InvalidTag:
        pass
    else:
        raise AssertionError("decrypt should have raised InvalidTag under a different key")

    # Restore the test key so other tests keep working.
    install_key_material("test-secret")


def test_at_rest_storage_is_encrypted(tmp_path):
    """End-to-end: writing an OAuth client_secret via upsert produces an
    AppConfig row whose `value` does NOT contain the plaintext."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.config_mgmt.models import AppConfig
    from backend.config_mgmt.oauth import OAuthProviderConfig, upsert_oauth_provider
    from backend.db.base import Base

    install_key_material("test-secret")

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        upsert_oauth_provider(
            db,
            OAuthProviderConfig(
                slug="google",
                name="Google",
                enabled=True,
                client_id="cid",
                client_secret="THE-REAL-GOOGLE-SECRET",
                issuer_url="",
            ),
        )

    with Session() as db:
        row = db.get(AppConfig, "oauth.google.client_secret")
        assert row is not None
        # The on-disk value is the AEAD envelope, NOT the plaintext.
        assert row.value.startswith("enc:v1:")
        assert "THE-REAL-GOOGLE-SECRET" not in row.value


def test_smtp_password_at_rest_is_encrypted(tmp_path):
    """End-to-end: writing SMTP password via upsert produces an AppConfig
    row whose `value` does NOT contain the plaintext."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.config_mgmt.models import AppConfig
    from backend.config_mgmt.smtp import SmtpConfig, upsert_smtp_config
    from backend.db.base import Base

    install_key_material("test-secret")

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        upsert_smtp_config(
            db,
            SmtpConfig(
                host="smtp.example.com",
                port=587,
                username="user",
                password="THE-REAL-SMTP-PASSWORD",
                from_address="a@b.c",
                use_tls=True,
            ),
        )

    with Session() as db:
        row = db.get(AppConfig, "smtp.password")
        assert row is not None
        assert row.value.startswith("enc:v1:")
        assert "THE-REAL-SMTP-PASSWORD" not in row.value
