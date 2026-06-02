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


def test_render_compose_returns_valid_yaml_referencing_env_file() -> None:
    import yaml as _yaml
    out = wizard.render_compose(host_port=8000, volume="skynetcontrol-data",
                                 env_file_name="skynetcontrol.env")
    parsed = _yaml.safe_load(out)
    svc = parsed["services"]["skynetcontrol"]
    assert svc["image"] == "ghcr.io/ben-kuhn/skynetcontrol:latest"
    assert svc["restart"] == "unless-stopped"
    assert svc["ports"] == ["8000:8000"]
    assert svc["volumes"] == ["skynetcontrol-data:/data"]
    assert svc["env_file"] == ["./skynetcontrol.env"]
    assert "skynetcontrol-data" in parsed["volumes"]


def test_render_compose_honors_custom_port_and_volume() -> None:
    import yaml as _yaml
    out = wizard.render_compose(host_port=9001, volume="custom-vol",
                                 env_file_name="custom.env")
    parsed = _yaml.safe_load(out)
    svc = parsed["services"]["skynetcontrol"]
    assert svc["ports"] == ["9001:8000"]
    assert svc["volumes"] == ["custom-vol:/data"]
    assert svc["env_file"] == ["./custom.env"]
    assert "custom-vol" in parsed["volumes"]


def _sample_env() -> dict[str, str]:
    return {
        "SKYNET_JWT_SECRET_KEY": "deadbeef" * 8,
        "SKYNET_APP_BASE_URL": "https://net.example.org",
        "SKYNET_AUTH_GITHUB_ENABLED": "true",
        "SKYNET_AUTH_GITHUB_CLIENT_ID": "Iv1.abc",
        "SKYNET_AUTH_GITHUB_CLIENT_SECRET": "ghs_xyz",
        "SKYNET_SMTP_HOST": "smtp.example.com",
        "SKYNET_SMTP_PASSWORD": "smtp-pass",
    }


def test_render_nix_module_flakes_uses_inputs_import() -> None:
    out = wizard.render_nix_module(_sample_env(), flakes=True,
                                    env_file_path="/run/skynetcontrol/env")
    assert '(import "${inputs.skynetcontrol}/module.nix")' in out
    assert "{ inputs, ... }:" in out


def test_render_nix_module_nonflake_uses_path_import() -> None:
    out = wizard.render_nix_module(_sample_env(), flakes=False,
                                    env_file_path="/run/skynetcontrol/env")
    assert "imports = [ /etc/nixos/skynetcontrol/module.nix ];" in out
    assert "{ inputs" not in out


def test_render_nix_module_inlines_plaintext_but_not_secrets() -> None:
    out = wizard.render_nix_module(_sample_env(), flakes=True,
                                    env_file_path="/run/skynetcontrol/env")
    # plaintext gets inlined under settings (with SKYNET_ prefix stripped)
    assert 'APP_BASE_URL = "https://net.example.org";' in out
    assert 'AUTH_GITHUB_ENABLED = "true";' in out
    assert 'AUTH_GITHUB_CLIENT_ID = "Iv1.abc";' in out
    assert 'SMTP_HOST = "smtp.example.com";' in out
    # secrets must not leak into the module text
    assert "deadbeef" not in out
    assert "ghs_xyz" not in out
    assert "smtp-pass" not in out
    assert "CLIENT_SECRET" not in out
    assert "JWT_SECRET_KEY" not in out
    assert "SMTP_PASSWORD" not in out


def test_render_nix_module_includes_environment_file_path() -> None:
    out = wizard.render_nix_module(_sample_env(), flakes=True,
                                    env_file_path="/run/skynetcontrol/env")
    assert "EnvironmentFile = [" in out
    assert '"/run/skynetcontrol/env"' in out


def test_render_nix_module_sorts_settings_for_stable_output() -> None:
    out = wizard.render_nix_module(_sample_env(), flakes=True,
                                    env_file_path="/run/skynetcontrol/env")
    settings_block = out.split("settings = {")[1].split("};")[0]
    keys = [line.strip().split(" =")[0]
            for line in settings_block.strip().splitlines() if "=" in line]
    assert keys == sorted(keys)
