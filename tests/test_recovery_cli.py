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


def test_cli_unknown_subcommand_exits_nonzero(monkeypatch, capsys, tmp_path):
    url, _ = _make_db(tmp_path)
    monkeypatch.setenv("SKYNET_DATABASE_URL", url)

    with pytest.raises(SystemExit) as exc_info:
        main(["no-such-command"])
    assert exc_info.value.code != 0
