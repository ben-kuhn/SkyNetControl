import os
import stat
from pathlib import Path

import pytest

from scripts import setup as wizard


def test_load_env_returns_empty_dict_when_missing(tmp_path: Path) -> None:
    assert wizard.load_env(tmp_path / "missing.env") == {}


def test_load_env_parses_key_value_pairs(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("SKYNET_A=one\n# comment\n\nSKYNET_B=two=with=equals\n")
    assert wizard.load_env(p) == {
        "SKYNET_A": "one",
        "SKYNET_B": "two=with=equals",
    }


def test_load_env_ignores_blank_and_comment_lines(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("\n# top comment\nSKYNET_X=1\n  # indented comment\n")
    assert wizard.load_env(p) == {"SKYNET_X": "1"}


def test_save_env_writes_keys_and_sets_0600(tmp_path: Path) -> None:
    p = tmp_path / "out.env"
    wizard.save_env({"SKYNET_A": "1", "SKYNET_B": "two"}, p)
    assert p.read_text() == "SKYNET_A=1\nSKYNET_B=two\n"
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_save_env_overwrites_existing(tmp_path: Path) -> None:
    p = tmp_path / "out.env"
    p.write_text("OLD=value\n")
    wizard.save_env({"NEW": "value"}, p)
    assert p.read_text() == "NEW=value\n"


def test_providers_cover_all_supported_names() -> None:
    names = {p["name"] for p in wizard.PROVIDERS}
    assert names == {"Google", "GitHub", "Microsoft", "Discord", "Facebook", "Generic OIDC"}


def test_provider_prefixes_match_docs() -> None:
    by_name = {p["name"]: p for p in wizard.PROVIDERS}
    assert by_name["GitHub"]["prefix"] == "SKYNET_AUTH_GITHUB_"
    assert by_name["Google"]["prefix"] == "SKYNET_AUTH_GOOGLE_"
    assert by_name["Microsoft"]["prefix"] == "SKYNET_AUTH_MICROSOFT_"
    assert by_name["Discord"]["prefix"] == "SKYNET_AUTH_DISCORD_"
    assert by_name["Facebook"]["prefix"] == "SKYNET_AUTH_FACEBOOK_"
    assert by_name["Generic OIDC"]["prefix"] == "SKYNET_AUTH_OIDC_"


def test_only_generic_oidc_has_issuer_url() -> None:
    by_name = {p["name"]: p for p in wizard.PROVIDERS}
    assert by_name["Generic OIDC"]["extra"] == ["ISSUER_URL"]
    for name in ("Google", "GitHub", "Microsoft", "Discord", "Facebook"):
        assert by_name[name]["extra"] == []


@pytest.mark.parametrize("key", [
    "SKYNET_JWT_SECRET_KEY",
    "SKYNET_AUTH_GITHUB_CLIENT_SECRET",
    "SKYNET_AUTH_GOOGLE_CLIENT_SECRET",
    "SKYNET_AUTH_OIDC_CLIENT_SECRET",
    "SKYNET_SMTP_PASSWORD",
])
def test_is_secret_key_true_for_secrets(key: str) -> None:
    assert wizard.is_secret_key(key) is True


@pytest.mark.parametrize("key", [
    "SKYNET_APP_BASE_URL",
    "SKYNET_AUTH_GITHUB_ENABLED",
    "SKYNET_AUTH_GITHUB_CLIENT_ID",
    "SKYNET_AUTH_OIDC_ISSUER_URL",
    "SKYNET_SMTP_HOST",
    "SKYNET_SMTP_PORT",
    "SKYNET_SMTP_USERNAME",
    "SKYNET_SMTP_FROM_ADDRESS",
    "SKYNET_SMTP_USE_TLS",
])
def test_is_secret_key_false_for_plaintext(key: str) -> None:
    assert wizard.is_secret_key(key) is False
