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
