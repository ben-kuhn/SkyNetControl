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
import secrets as _secrets
import sys
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
        "extra": [],
        "console_url": "https://console.cloud.google.com/apis/credentials",
    },
    {
        "name": "GitHub",
        "prefix": "SKYNET_AUTH_GITHUB_",
        "extra": [],
        "console_url": "https://github.com/settings/developers",
    },
    {
        "name": "Microsoft",
        "prefix": "SKYNET_AUTH_MICROSOFT_",
        "extra": [],
        "console_url": "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps",
    },
    {
        "name": "Discord",
        "prefix": "SKYNET_AUTH_DISCORD_",
        "extra": [],
        "console_url": "https://discord.com/developers/applications",
    },
    {
        "name": "Facebook",
        "prefix": "SKYNET_AUTH_FACEBOOK_",
        "extra": [],
        "console_url": "https://developers.facebook.com/apps/",
    },
    {
        "name": "Generic OIDC",
        "prefix": "SKYNET_AUTH_OIDC_",
        "extra": ["ISSUER_URL"],
        "console_url": "(your IdP's app-registration UI)",
    },
]


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
        value = env[key].replace("\\", "\\\\").replace('"', '\\"')
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
    return [p for p in PROVIDERS if env.get(f"{p['prefix']}ENABLED") == "true"]


def _disabled_providers(env: dict[str, str]) -> list[dict]:
    enabled = {p["name"] for p in _enabled_providers(env)}
    return [p for p in PROVIDERS if p["name"] not in enabled]


def _configure_provider(provider: dict, env: dict[str, str]) -> None:
    """Prompt for one provider's credentials and write them into env."""
    from prompt_toolkit import prompt
    from prompt_toolkit.formatted_text import HTML

    prefix = provider["prefix"]
    print(f"\n  Configuring {provider['name']}")
    print(f"  Developer console: {provider['console_url']}")

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


def step_oidc(env: dict[str, str]) -> None:
    """Step 2: Add/edit/remove OIDC providers in a loop."""
    from prompt_toolkit import prompt

    print("\n" + "=" * 60)
    print("Step 2/4: OIDC providers")
    print("=" * 60)
    print("  At least one provider must be enabled before the backend will start.")

    while True:
        enabled = _enabled_providers(env)
        names = ", ".join(p["name"] for p in enabled) if enabled else "none"
        print(f"\n  Currently enabled: {names}")
        action = prompt("  Action [a]dd / [e]dit / [r]emove / [d]one: ").strip().lower() or "d"

        if action == "d":
            if not enabled:
                confirm = prompt("  No providers enabled. Continue anyway? [y/N]: ").strip().lower()
                if confirm != "y":
                    continue
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
            idx = _choose([p["name"] for p in enabled], "Pick a provider to edit")
            if idx is None:
                continue
            _configure_provider(enabled[idx], env)

        elif action == "r":
            if not enabled:
                print("  No providers to remove.")
                continue
            idx = _choose([p["name"] for p in enabled], "Pick a provider to remove")
            if idx is None:
                continue
            _remove_provider(enabled[idx], env)
            print(f"  Removed {enabled[idx]['name']}.")

        else:
            print(f"  Unknown action: {action!r}")


def _check_optional_deps() -> None:
    """Print install instructions and exit if prompt_toolkit/pyyaml are missing."""
    missing = []
    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        missing.append("prompt_toolkit")
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("pyyaml")
    if missing:
        joined = ", ".join(missing)
        print(f"Missing required setup dependencies: {joined}", file=sys.stderr)
        print('Install with:  pip install -e ".[setup]"', file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="SkyNetControl setup wizard")
    parser.add_argument("--env-file", default="skynetcontrol.env",
                        help="Path to the env file to read/write (default: skynetcontrol.env)")
    parser.add_argument("--compose-file", default="docker-compose.yml",
                        help="Path to write docker-compose output (default: docker-compose.yml)")
    parser.add_argument("--nix-file", default="skynetcontrol.nix",
                        help="Path to write Nix module snippet (default: skynetcontrol.nix)")
    args = parser.parse_args()

    _check_optional_deps()

    # Step wiring comes in Task 9.
    print("Setup wizard scaffold OK")
    print(f"  env file:     {Path(args.env_file).resolve()}")
    print(f"  compose file: {Path(args.compose_file).resolve()}")
    print(f"  nix file:     {Path(args.nix_file).resolve()}")


if __name__ == "__main__":
    main()
