"""Build-time version constants.

`VERSION` is the package version (pyproject.toml is the source of truth,
mirrored here so callers don't have to round-trip through importlib).

`GIT_SHA` is the short commit hash baked in at Nix build time via the
`SKYNET_GIT_SHA` env var (see default.nix's makeWrapperArgs). Falls back
to `"dev"` when running outside a Nix build (e.g. `run-dev.sh`) so the
admin sidebar can distinguish "release artifact at commit X" from
"running on a developer's machine".
"""
import os

VERSION = "0.1.0"
GIT_SHA = os.environ.get("SKYNET_GIT_SHA") or "dev"
