#!/usr/bin/env python3
"""SkyNetControl setup wizard.

Walks an operator through producing skynetcontrol.env plus one of:
  - docker-compose.yml
  - skynetcontrol.nix (NixOS module snippet, flake-based)
  - skynetcontrol.nix (NixOS module snippet, non-flake)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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
