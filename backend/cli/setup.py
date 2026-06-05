#!/usr/bin/env python3
"""SkyNetControl setup wizard.

Walks an operator through producing skynetcontrol.env plus one of:
  - docker-compose.yml
  - skynetcontrol.nix (NixOS module snippet, flake-based)
  - skynetcontrol.nix (NixOS module snippet, non-flake)
"""

from __future__ import annotations

import argparse
import os
import re
import secrets as _secrets
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    """Load a KEY=value env file into a dict. Returns {} if the file is missing."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value
    return result


def save_env(values: dict[str, str], path: Path) -> None:
    """Write `values` to `path` as KEY=value lines, mode 0o600.

    Replaces the file's full contents — callers must merge changes in themselves.
    """
    content = "".join(f"{k}={v}\n" for k, v in values.items())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode())
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        # bind-mounted file under rootless containers: not fatal
        pass


PROVIDERS: list[dict] = [
    {
        "name": "Google",
        "prefix": "SKYNET_AUTH_GOOGLE_",
        "slug": "google",
        "extra": [],
        "console_url": "https://console.cloud.google.com/apis/credentials",
    },
    {
        "name": "GitHub",
        "prefix": "SKYNET_AUTH_GITHUB_",
        "slug": "github",
        "extra": [],
        "console_url": "https://github.com/settings/developers",
    },
    {
        "name": "Microsoft",
        "prefix": "SKYNET_AUTH_MICROSOFT_",
        "slug": "microsoft",
        "extra": [],
        "console_url": "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps",
    },
    {
        "name": "Discord",
        "prefix": "SKYNET_AUTH_DISCORD_",
        "slug": "discord",
        "extra": [],
        "console_url": "https://discord.com/developers/applications",
    },
    {
        "name": "Facebook",
        "prefix": "SKYNET_AUTH_FACEBOOK_",
        "slug": "facebook",
        "extra": [],
        "console_url": "https://developers.facebook.com/apps/",
    },
    {
        "name": "Generic OIDC",
        "prefix": "SKYNET_AUTH_OIDC_",  # template only — real providers get a slugged prefix
        "slug": "oidc",
        "extra": ["ISSUER_URL"],
        "console_url": "(your IdP's app-registration UI)",
        "is_template": True,
    },
]

_OIDC_ENV_RE = re.compile(
    r"^SKYNET_AUTH_OIDC_([A-Z0-9_]+)_(NAME|ENABLED|CLIENT_ID|CLIENT_SECRET|ISSUER_URL)$"
)


def _oidc_providers_from_env(env: dict[str, str]) -> list[dict]:
    """Return one descriptor per OIDC provider present in env.

    Each descriptor mirrors a PROVIDERS entry but with `slug`, `prefix`,
    `is_oidc=True`. NAME defaults to title-cased slug when absent.
    """
    from backend.auth.oidc_slug import slug_from_env_middle  # local: avoid cycle

    seen: dict[str, str] = {}  # slug -> env middle (e.g. "AUTHENTIK")
    names: dict[str, str] = {}
    for key in env:
        m = _OIDC_ENV_RE.match(key)
        if not m:
            continue
        middle = m.group(1)
        slug = slug_from_env_middle(middle)
        seen.setdefault(slug, middle)
        if m.group(2) == "NAME":
            names[slug] = env[key]

    descriptors = []
    for slug in sorted(seen):
        middle = seen[slug]
        descriptors.append({
            "name": names.get(slug, slug.title()),
            "slug": slug,
            "prefix": f"SKYNET_AUTH_OIDC_{middle}_",
            "extra": ["ISSUER_URL"],
            "console_url": "(your IdP's app-registration UI)",
            "is_oidc": True,
        })
    return descriptors


def is_secret_key(key: str) -> bool:
    """True if `key` must never be inlined into a Nix module (must go in env file)."""
    if key == "SKYNET_JWT_SECRET_KEY":
        return True
    if key == "SKYNET_SMTP_PASSWORD":
        return True
    if key.startswith("SKYNET_AUTH_") and key.endswith("_CLIENT_SECRET"):
        return True
    return False


def render_compose(host_port: int, volume: str, env_file_name: str) -> str:
    """Render a docker-compose.yml string for skynetcontrol with the given settings."""
    import yaml  # local import — pyyaml is in the setup extra

    compose = {
        "services": {
            "skynetcontrol": {
                "image": "ghcr.io/ben-kuhn/skynetcontrol:latest",
                "restart": "unless-stopped",
                "ports": [f"{host_port}:8000"],
                "volumes": [f"{volume}:/data"],
                "env_file": [f"./{env_file_name}"],
            }
        },
        "volumes": {volume: None},
    }
    return yaml.dump(compose, default_flow_style=False, sort_keys=False)


def render_nix_module(env: dict[str, str], *, flakes: bool,
                      env_file_path: str) -> str:
    """Render a NixOS module snippet for services.skynetcontrol.

    Plaintext SKYNET_* keys go inline under `settings`. Secrets are NOT
    rendered — the caller is expected to write them to `env_file_path`
    separately.
    """
    settings_lines = []
    for key in sorted(env.keys()):
        if not key.startswith("SKYNET_"):
            continue
        if is_secret_key(key):
            continue
        nix_key = key[len("SKYNET_"):]
        value = (
            env[key]
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("${", "''${")
        )
        settings_lines.append(f'        {nix_key} = "{value}";')
    settings_block = "\n".join(settings_lines) if settings_lines else "        # (no plaintext settings configured)"

    if flakes:
        header = "{ inputs, ... }: {\n  imports = [ (import \"${inputs.skynetcontrol}/module.nix\") ];"
    else:
        header = "{ ... }: {\n  imports = [ /etc/nixos/skynetcontrol/module.nix ];"

    return f"""{header}

  services.skynetcontrol = {{
    enable = true;
    settings = {{
{settings_block}
    }};
  }};

  systemd.services.skynetcontrol.serviceConfig.EnvironmentFile = [
    "{env_file_path}"
  ];
}}
"""


def _masked(value: str) -> str:
    """Return a masked display for a secret default — e.g. ***abcd12."""
    if not value:
        return "(not set)"
    return f"***{value[-6:]}" if len(value) > 6 else "(set)"


def step_core(env: dict[str, str]) -> None:
    """Step 1: Ensure JWT secret exists; prompt for APP_BASE_URL."""
    from prompt_toolkit import prompt
    from prompt_toolkit.formatted_text import HTML

    print("\n" + "=" * 60)
    print("Step 1/4: Core settings")
    print("=" * 60)

    if not env.get("SKYNET_JWT_SECRET_KEY"):
        env["SKYNET_JWT_SECRET_KEY"] = _secrets.token_hex(32)
        print("  Generated new SKYNET_JWT_SECRET_KEY")
    else:
        print(f"  Existing SKYNET_JWT_SECRET_KEY kept ({_masked(env['SKYNET_JWT_SECRET_KEY'])})")

    current_url = env.get("SKYNET_APP_BASE_URL", "http://localhost:8000")
    url = prompt(
        HTML(f"APP_BASE_URL [<ansigreen>{current_url}</ansigreen>]: "),
    ).strip() or current_url
    env["SKYNET_APP_BASE_URL"] = url.rstrip("/")


def _enabled_providers(env: dict[str, str]) -> list[dict]:
    fixed = [p for p in PROVIDERS
             if not p.get("is_template")
             and env.get(f"{p['prefix']}ENABLED") == "true"]
    oidc = [p for p in _oidc_providers_from_env(env)
            if env.get(f"{p['prefix']}ENABLED") == "true"]
    return fixed + oidc


def _disabled_providers(env: dict[str, str]) -> list[dict]:
    enabled_slugs = {p["slug"] for p in _enabled_providers(env)}
    out = []
    for p in PROVIDERS:
        if p.get("is_template"):
            out.append(p)  # always available to add another OIDC
        elif p["slug"] not in enabled_slugs:
            out.append(p)
    return out


def _configure_provider(provider: dict, env: dict[str, str]) -> None:
    """Prompt for one provider's credentials and write them into env.

    For the Generic OIDC template (is_template=True), this adds a *new*
    OIDC provider: prompts for a friendly name, derives a slug, then
    re-dispatches to itself with the new provider descriptor.
    """
    from prompt_toolkit import prompt
    from prompt_toolkit.formatted_text import HTML

    from backend.auth.oidc_slug import (
        env_middle_from_slug, slugify, validate_slug,
    )

    if provider.get("is_template"):
        # Step 1: friendly name
        while True:
            name = prompt(HTML("\n  Friendly name for this OIDC provider "
                               "(e.g. <ansigreen>Authentik</ansigreen>): ")).strip()
            if name:
                break
            print("  Name is required.")

        # Step 2: slug — default from name, editable, validated, unique
        existing_slugs = {p["slug"] for p in _oidc_providers_from_env(env)}
        default_slug = slugify(name)
        while True:
            slug = prompt(HTML(f"  URL slug [<ansigreen>{default_slug}</ansigreen>]: ")).strip() or default_slug
            err = validate_slug(slug)
            if err is None and slug in existing_slugs:
                err = f"'{slug}' is already configured — pick another or [r]emove the existing one first"
            if err is None:
                break
            print(f"  {err}")
            default_slug = slug  # let user edit their own input next round

        # Now act as if we were configuring a discovered OIDC provider.
        new_descriptor = {
            "name": name,
            "slug": slug,
            "prefix": f"SKYNET_AUTH_OIDC_{env_middle_from_slug(slug)}_",
            "extra": ["ISSUER_URL"],
            "console_url": "(your IdP's app-registration UI)",
            "is_oidc": True,
        }
        env[f"{new_descriptor['prefix']}NAME"] = name
        _configure_provider(new_descriptor, env)
        return

    # Existing OIDC provider OR fixed provider — same flow.
    prefix = provider["prefix"]
    base_url = env.get("SKYNET_APP_BASE_URL", "http://localhost:8000").rstrip("/")
    redirect_uri = f"{base_url}/api/auth/callback/{provider['slug']}"

    print(f"\n  Configuring {provider['name']}")
    print(f"  Developer console: {provider['console_url']}")
    print(f"  Set the OAuth redirect / callback URL there to:")
    print(f"    {redirect_uri}")

    # OIDC providers (not templates): allow renaming.
    if provider.get("is_oidc"):
        cur_name = env.get(f"{prefix}NAME", provider["name"])
        new_name = prompt(
            HTML(f"  Friendly name [<ansigreen>{cur_name}</ansigreen>]: "),
        ).strip() or cur_name
        env[f"{prefix}NAME"] = new_name

    cur_id = env.get(f"{prefix}CLIENT_ID", "")
    cur_secret = env.get(f"{prefix}CLIENT_SECRET", "")

    id_hint = cur_id or "(not set)"
    new_id = prompt(HTML(f"  Client ID [<ansigreen>{id_hint}</ansigreen>]: ")).strip() or cur_id
    new_secret = prompt(
        HTML(f"  Client secret [<ansigreen>{_masked(cur_secret)}</ansigreen>]: "),
        is_password=True,
    ).strip() or cur_secret

    env[f"{prefix}ENABLED"] = "true"
    env[f"{prefix}CLIENT_ID"] = new_id
    env[f"{prefix}CLIENT_SECRET"] = new_secret

    for extra in provider["extra"]:
        cur_extra = env.get(f"{prefix}{extra}", "")
        new_extra = prompt(
            HTML(f"  {extra} [<ansigreen>{cur_extra or '(not set)'}</ansigreen>]: "),
        ).strip() or cur_extra
        env[f"{prefix}{extra}"] = new_extra


def _remove_provider(provider: dict, env: dict[str, str]) -> None:
    """Strip all env keys for a provider."""
    prefix = provider["prefix"]
    for key in list(env.keys()):
        if key.startswith(prefix):
            del env[key]


def _choose(options: list[str], prompt_text: str) -> int | None:
    """Show a numbered menu, return chosen 0-based index, or None on empty/invalid."""
    from prompt_toolkit import prompt

    if not options:
        return None
    for i, name in enumerate(options, 1):
        print(f"    {i}) {name}")
    raw = prompt(f"  {prompt_text}: ").strip()
    if not raw.isdigit():
        return None
    idx = int(raw) - 1
    if 0 <= idx < len(options):
        return idx
    return None


def _enabled_label(provider: dict) -> str:
    """Display label for a provider in the wizard's enabled list."""
    if provider.get("is_oidc"):
        return f"{provider['name']} (oidc: {provider['slug']})"
    return provider["name"]


def _print_redirect_recap(enabled: list[dict], base_url: str) -> None:
    """Print a recap of redirect URIs for the currently-enabled providers."""
    if not enabled:
        return
    width = max(len(p["name"]) for p in enabled) + 2
    print("\n  Redirect URIs to configure in your provider consoles:")
    for p in enabled:
        label = (p["name"] + ":").ljust(width)
        print(f"    {label} {base_url}/api/auth/callback/{p['slug']}")


def step_oidc(env: dict[str, str]) -> None:
    """Step 2: Add/edit/remove OIDC providers in a loop."""
    from prompt_toolkit import prompt

    base_url = env.get("SKYNET_APP_BASE_URL", "http://localhost:8000").rstrip("/")

    print("\n" + "=" * 60)
    print("Step 2/4: OIDC providers")
    print("=" * 60)
    print("  At least one provider must be enabled before the backend will start.")
    print("  Redirect URI pattern (configure these in each provider's developer console):")
    print(f"    {base_url}/api/auth/callback/<provider>")

    while True:
        enabled = _enabled_providers(env)
        if enabled:
            names = ", ".join(_enabled_label(p) for p in enabled)
        else:
            names = "none"
        print(f"\n  Currently enabled: {names}")
        action = prompt("  Action [a]dd / [e]dit / [r]emove / [d]one: ").strip().lower() or "d"

        if action == "d":
            if not enabled:
                confirm = prompt("  No providers enabled. Continue anyway? [y/N]: ").strip().lower()
                if confirm != "y":
                    continue
            _print_redirect_recap(enabled, base_url)
            return

        if action == "a":
            candidates = _disabled_providers(env)
            if not candidates:
                print("  All supported providers are already enabled.")
                continue
            idx = _choose([p["name"] for p in candidates], "Pick a provider to add")
            if idx is None:
                continue
            _configure_provider(candidates[idx], env)

        elif action == "e":
            if not enabled:
                print("  No providers to edit.")
                continue
            idx = _choose([_enabled_label(p) for p in enabled], "Pick a provider to edit")
            if idx is None:
                continue
            _configure_provider(enabled[idx], env)

        elif action == "r":
            if not enabled:
                print("  No providers to remove.")
                continue
            idx = _choose([_enabled_label(p) for p in enabled], "Pick a provider to remove")
            if idx is None:
                continue
            _remove_provider(enabled[idx], env)
            print(f"  Removed {_enabled_label(enabled[idx])}.")

        else:
            print(f"  Unknown action: {action!r}")


def step_smtp(env: dict[str, str]) -> None:
    """Step 3: optional SMTP configuration."""
    from prompt_toolkit import prompt
    from prompt_toolkit.formatted_text import HTML

    print("\n" + "=" * 60)
    print("Step 3/4: SMTP (optional — for email notifications)")
    print("=" * 60)

    currently_on = bool(env.get("SKYNET_SMTP_HOST"))
    default = "Y/n" if currently_on else "y/N"
    raw = prompt(f"  Configure SMTP? [{default}]: ").strip().lower()
    enable = (raw == "y") if raw in {"y", "n"} else currently_on

    if not enable:
        # If SMTP was previously configured and user opts out, leave keys alone
        # so they aren't lost — they just won't be touched.
        print("  Skipping SMTP step.")
        return

    fields = [
        ("SKYNET_SMTP_HOST", "Host", "", False),
        ("SKYNET_SMTP_PORT", "Port", "587", False),
        ("SKYNET_SMTP_USERNAME", "Username", "", False),
        ("SKYNET_SMTP_PASSWORD", "Password", "", True),
        ("SKYNET_SMTP_FROM_ADDRESS", "From address", "", False),
        ("SKYNET_SMTP_USE_TLS", "Use TLS (true/false)", "true", False),
    ]
    for key, label, default_val, is_password in fields:
        current = env.get(key, default_val)
        hint = _masked(current) if is_password else (current or "(not set)")
        new = prompt(
            HTML(f"  {label} [<ansigreen>{hint}</ansigreen>]: "),
            is_password=is_password,
        ).strip() or current
        env[key] = new


def _split_secrets(env: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Return (secret_env, plaintext_env) — both restricted to SKYNET_* keys."""
    secret: dict[str, str] = {}
    plaintext: dict[str, str] = {}
    for k, v in env.items():
        if not k.startswith("SKYNET_"):
            continue
        if is_secret_key(k):
            secret[k] = v
        else:
            plaintext[k] = v
    return secret, plaintext


def _resolve_output_path(requested: Path, label: str) -> Path:
    """If `requested` already exists, swap in a `.generated` filename and warn."""
    if not requested.exists():
        return requested
    fallback = requested.with_name(requested.stem + ".generated" + requested.suffix)
    print(f"  {label} already exists at {requested} — writing to {fallback} instead.")
    print(f"  Review the diff, then: mv {fallback} {requested}")
    return fallback


def step_output(env: dict[str, str], env_path: Path,
                compose_path: Path, nix_path: Path) -> None:
    """Step 4: write skynetcontrol.env + chosen deployment artifact."""
    from prompt_toolkit import prompt
    from prompt_toolkit.formatted_text import HTML

    print("\n" + "=" * 60)
    print("Step 4/4: Output format")
    print("=" * 60)
    print("  1) docker-compose.yml")
    print("  2) NixOS module (with flakes)")
    print("  3) NixOS module (without flakes)")

    while True:
        raw = prompt("  Pick a format [1/2/3]: ").strip()
        if raw in {"1", "2", "3"}:
            choice = raw
            break
        print("  Please enter 1, 2, or 3.")

    if choice == "1":
        # docker-compose: full env file + compose
        host_port_raw = prompt(HTML("  Host port [<ansigreen>8000</ansigreen>]: ")).strip() or "8000"
        if not host_port_raw.isdigit() or not (1 <= int(host_port_raw) <= 65535):
            print(f"  Invalid port {host_port_raw!r}, defaulting to 8000.")
            host_port = 8000
        else:
            host_port = int(host_port_raw)
        volume = prompt(HTML("  Docker volume name [<ansigreen>skynetcontrol-data</ansigreen>]: ")).strip() or "skynetcontrol-data"

        full_env = {k: v for k, v in env.items() if k.startswith("SKYNET_")}
        save_env(full_env, env_path)
        print(f"  Wrote {env_path} (mode 0600, {len(full_env)} keys)")

        target = _resolve_output_path(compose_path, "docker-compose.yml")
        target.write_text(render_compose(host_port=host_port, volume=volume,
                                          env_file_name=env_path.name))
        print(f"  Wrote {target}")
        print("\n  To start:  docker compose up -d")
        return

    # Nix branches: secrets-only env file + module snippet
    secret_env, _ = _split_secrets(env)
    save_env(secret_env, env_path)
    print(f"  Wrote {env_path} (mode 0600, {len(secret_env)} secret keys)")

    env_file_default = "/run/skynetcontrol/env"
    env_file_path = prompt(
        HTML(f"  Path the EnvironmentFile will live at on the target host "
             f"[<ansigreen>{env_file_default}</ansigreen>]: "),
    ).strip() or env_file_default

    flakes = (choice == "2")
    target = _resolve_output_path(nix_path, "skynetcontrol.nix")
    target.write_text(render_nix_module(env, flakes=flakes, env_file_path=env_file_path))
    print(f"  Wrote {target}")

    if flakes:
        print('\n  Reminder: add this to your flake inputs:')
        print('    skynetcontrol.url = "github:ben-kuhn/SkyNetControl";')
        print(f'  Then import {target.name} from your nixosConfigurations module list.')
    else:
        print("\n  Reminder: clone the project onto the target host:")
        print("    git clone https://github.com/ben-kuhn/SkyNetControl /etc/nixos/skynetcontrol")
        print(f"  Then add {target.name} to your imports in configuration.nix.")
    print(f"  Move {env_path.name} into your secret store (sops-nix, agenix, etc.)")
    print(f"  and have it materialise at {env_file_path}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="SkyNetControl setup wizard")
    parser.add_argument("--env-file", default="skynetcontrol.env",
                        help="Path to the env file to read/write (default: skynetcontrol.env)")
    parser.add_argument("--compose-file", default="docker-compose.yml",
                        help="Path to write docker-compose output (default: docker-compose.yml)")
    parser.add_argument("--nix-file", default="skynetcontrol.nix",
                        help="Path to write Nix module snippet (default: skynetcontrol.nix)")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    compose_path = Path(args.compose_file)
    nix_path = Path(args.nix_file)

    print("=" * 60)
    print("  SkyNetControl setup wizard")
    print("=" * 60)
    if env_path.exists():
        print(f"  Existing env loaded from {env_path} — values will pre-fill.")
    else:
        print(f"  No existing env at {env_path} — starting fresh.")

    env = load_env(env_path)

    step_core(env)
    step_oidc(env)
    step_smtp(env)
    step_output(env, env_path, compose_path, nix_path)

    print("\n" + "=" * 60)
    print("  Setup complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
