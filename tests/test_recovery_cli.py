"""Tests for the skynetcontrol-recovery CLI (backend.cli.recovery:main)."""
from __future__ import annotations

import hashlib

import pytest

from backend.cli.recovery import main
from backend.auth.recovery import verify_token, list_outstanding, _hash
from backend.db.session import create_engine_from_url, create_session_factory
from backend.db.base import Base
import backend.auth.models  # noqa: F401 — ensure AdminRecoveryToken is registered


def _make_db(tmp_path):
    """Create an in-file SQLite DB with the full schema applied."""
    url = f"sqlite:///{tmp_path}/r.db"
    engine = create_engine_from_url(url)
    Base.metadata.create_all(engine)
    return url, engine


def test_cli_mint_prints_token_and_url(tmp_path, capsys, monkeypatch):
    url, _ = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    rc = main(["mint-admin-token"])

    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "Token (use it once):" in out
    assert "Claim URL:" in out
    assert "/recovery?token=" in out
    assert "Hash prefix:" in out
    assert "Expires:" in out
    assert "ONCE" in out


def test_cli_mint_persists_token_in_db(tmp_path, monkeypatch, capsys):
    url, engine = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    main(["mint-admin-token"])

    # Extract the plaintext from stdout and verify it round-trips
    captured = capsys.readouterr()
    plaintext = None
    for line in captured.out.splitlines():
        if line.startswith("Token (use it once):"):
            plaintext = line.split(":", 1)[1].strip()
            break
    assert plaintext is not None

    session_factory = create_session_factory(engine)
    with session_factory() as db:
        row = verify_token(db, plaintext)
    assert row is not None


def test_cli_list_tokens_shows_outstanding_only(tmp_path, capsys, monkeypatch):
    url, _ = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    # Mint two tokens
    main(["mint-admin-token"])
    main(["mint-admin-token"])
    capsys.readouterr()  # flush stdout so far

    rc = main(["list-tokens"])
    assert rc == 0
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip() and ln[0] != "H"]
    # At least 2 data rows (one per token)
    assert len(lines) >= 2


def test_cli_list_tokens_does_not_print_plaintext(tmp_path, capsys, monkeypatch):
    url, engine = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    # Mint and capture the plaintext
    main(["mint-admin-token"])
    captured = capsys.readouterr()
    plaintext = None
    for line in captured.out.splitlines():
        if line.startswith("Token (use it once):"):
            plaintext = line.split(":", 1)[1].strip()
            break
    assert plaintext is not None

    # Now list — plaintext must NOT appear
    main(["list-tokens"])
    list_out = capsys.readouterr().out
    assert plaintext not in list_out


def test_cli_revoke_marks_token_used(tmp_path, capsys, monkeypatch):
    url, engine = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    main(["mint-admin-token"])
    captured = capsys.readouterr()
    plaintext = None
    for line in captured.out.splitlines():
        if line.startswith("Token (use it once):"):
            plaintext = line.split(":", 1)[1].strip()
            break
    assert plaintext is not None

    prefix = hashlib.sha256(plaintext.encode()).hexdigest()[:8]
    rc = main(["revoke", prefix])
    assert rc == 0
    capsys.readouterr()

    session_factory = create_session_factory(engine)
    with session_factory() as db:
        row = verify_token(db, plaintext)
    assert row is None  # revoked, so verify returns None


def test_cli_revoke_unknown_prefix_exits_0(tmp_path, capsys, monkeypatch):
    url, _ = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    rc = main(["revoke", "deadbeef"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 token(s)" in out


def test_cli_rotate_secrets_re_encrypts_plaintext_rows(tmp_path, monkeypatch, capsys):
    """rotate-secrets walks AppConfig and re-envelopes any plaintext
    oauth/smtp credential rows. Idempotent — already-encrypted rows pass
    through. Non-sensitive rows are left untouched.
    """
    url, engine = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)
    monkeypatch.setenv("SKYNET_JWT_SECRET_KEY", "test-secret")

    # Hand-seed: one plaintext sensitive row, one already-encrypted
    # sensitive row, one non-sensitive row.
    from backend.auth.secret_box import _PREFIX, encrypt, install_key_material
    from backend.config_mgmt.models import AppConfig
    install_key_material("test-secret")
    factory = create_session_factory(engine)
    with factory() as db:
        db.add(AppConfig(key="oauth.google.client_secret", value="plain-google-secret"))
        db.add(AppConfig(key="smtp.password", value=encrypt("already-protected")))
        db.add(AppConfig(key="net_address", value="w0ne@winlink.org"))
        db.commit()

    rc = main(["rotate-secrets"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Re-encrypted 1 row(s) from plaintext" in out
    assert "1 were already encrypted under current key" in out

    with factory() as db:
        google = db.get(AppConfig, "oauth.google.client_secret")
        smtp = db.get(AppConfig, "smtp.password")
        net = db.get(AppConfig, "net_address")
        assert google.value.startswith(_PREFIX)
        assert "plain-google-secret" not in google.value
        # Already-encrypted row is unchanged byte-for-byte.
        from backend.auth.secret_box import decrypt
        assert decrypt(smtp.value) == "already-protected"
        # Non-sensitive row is untouched.
        assert net.value == "w0ne@winlink.org"

    # Second run is idempotent — nothing left to re-encrypt.
    rc = main(["rotate-secrets"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Re-encrypted 0 row(s)" in out
    assert "2 were already encrypted under current key" in out


def test_cli_rotate_secrets_from_key_migrates_old_envelope(tmp_path, monkeypatch, capsys):
    """--from-key migrates rows encrypted under the previous key.

    Workflow: install old key, encrypt a row, then install new key (sim
    rotation), run rotate-secrets --from-key OLD. The row should be
    decrypted under OLD and re-encrypted under the current key, with the
    plaintext preserved through the round-trip.
    """
    url, engine = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    from backend.auth.secret_box import _PREFIX, decrypt, encrypt, install_key_material
    from backend.config_mgmt.models import AppConfig

    # Encrypt under the OLD key directly into the DB.
    install_key_material("old-secret-key")
    factory = create_session_factory(engine)
    with factory() as db:
        db.add(AppConfig(key="oauth.google.client_secret", value=encrypt("ORIGINAL-PLAINTEXT")))
        db.commit()

    # Now rotate: install the NEW key as if the operator changed env.
    monkeypatch.setenv("SKYNET_JWT_SECRET_KEY", "new-secret-key")
    # Without --from-key, the row is unrecoverable. With it, it migrates.
    rc = main(["rotate-secrets", "--from-key", "old-secret-key"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "migrated 1" in out

    # Re-bind the current key in this test process (the CLI bound it
    # via its own install_key_material call) and verify the row now
    # decrypts under the new key.
    install_key_material("new-secret-key")
    with factory() as db:
        row = db.get(AppConfig, "oauth.google.client_secret")
        assert row.value.startswith(_PREFIX)
        assert decrypt(row.value) == "ORIGINAL-PLAINTEXT"


def test_cli_rotate_secrets_without_from_key_warns_on_unrecoverable(tmp_path, monkeypatch, capsys):
    """Without --from-key, an old-key envelope is flagged as unrecoverable
    rather than silently re-encrypting garbage."""
    url, engine = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    from backend.auth.secret_box import encrypt, install_key_material
    from backend.config_mgmt.models import AppConfig

    install_key_material("old-secret-key")
    factory = create_session_factory(engine)
    with factory() as db:
        db.add(AppConfig(key="oauth.google.client_secret", value=encrypt("PLAINTEXT")))
        db.commit()

    monkeypatch.setenv("SKYNET_JWT_SECRET_KEY", "new-secret-key")
    rc = main(["rotate-secrets"])
    # Non-zero exit so scripted callers (cron jobs, CI rotation pipelines)
    # don't silently miss the unrecoverable rows — the stderr WARNING
    # spells out the recovery path.
    assert rc != 0
    captured = capsys.readouterr()
    # Stderr carries the warning so log scrapers see it; stdout has summary.
    assert "could not be decrypted" in captured.err


def test_cli_unknown_subcommand_exits_nonzero(monkeypatch, capsys, tmp_path):
    url, _ = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    with pytest.raises(SystemExit) as exc_info:
        main(["no-such-command"])
    assert exc_info.value.code != 0
