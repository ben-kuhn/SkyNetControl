# Setup Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/setup.py`, an interactive wizard that produces `skynetcontrol.env` plus a deployment artifact (docker-compose.yml, NixOS module with flakes, or NixOS module without flakes).

**Architecture:** Single Python file `scripts/setup.py` with pure-function helpers (env I/O, value classification, compose/nix renderers) plus four interactive `step_*` functions that use `prompt_toolkit`. Pure helpers are unit-tested; UI steps are manually tested. New `setup` optional-dependency extra in `pyproject.toml` keeps `prompt_toolkit` + `pyyaml` out of the main runtime deps.

**Tech Stack:** Python 3.12, `prompt_toolkit`, `pyyaml`, stdlib `secrets` / `argparse`.

---

## File Structure

**New files:**

| File | Responsibility |
|------|---------------|
| `scripts/setup.py` | Wizard entry point; env I/O, provider metadata, renderers, step functions, `main()` |
| `tests/test_setup.py` | Unit tests for env round-trip, value classification, renderers |

**Modified files:**

| File | Change |
|------|--------|
| `pyproject.toml` | Add `setup` optional-dependency extra |
| `shell.nix` | Install `.[dev,setup]` so nix-shell picks up the new deps |
| `README.md` | Mention the wizard in the Quick start section |

---

## Conventions used in this plan

- All `prompt_toolkit` imports live **inside** step functions, never at module top — keeps `import scripts.setup` working in tests when prompt_toolkit isn't installed (mirrors PacketQTH's `configure.py`).
- All file paths are absolute or repo-rooted, and the tests assume `pytest` is run from the repo root with `pip install -e ".[dev,setup]"` already done.

---

### Task 1: Add `setup` extra + nix-shell wiring

**Files:**
- Modify: `pyproject.toml`
- Modify: `shell.nix`

- [ ] **Step 1: Add the `setup` extra to pyproject.toml**

Edit `pyproject.toml`. Find the `[project.optional-dependencies]` block:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]
```

Replace with:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]
setup = [
    "prompt_toolkit>=3.0",
    "pyyaml>=6.0",
]
```

- [ ] **Step 2: Update shell.nix to install the new extra**

Edit `shell.nix`. Find this line:

```
      pip install -e ".[dev]" --quiet 2>/dev/null || true
```

Replace with:

```
      pip install -e ".[dev,setup]" --quiet 2>/dev/null || true
```

- [ ] **Step 3: Reinstall inside nix-shell so the new deps land in `.venv`**

Run (from inside `nix-shell`):

```bash
pip install -e ".[dev,setup]"
```

Expected: `prompt_toolkit` and `pyyaml` install successfully.

- [ ] **Step 4: Verify imports**

Run:

```bash
python -c "import prompt_toolkit, yaml; print('ok')"
```

Expected output: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml shell.nix
git commit -m "chore: add setup extra (prompt_toolkit + pyyaml) for setup wizard"
```

---

### Task 2: Script scaffold + missing-dep check

**Files:**
- Create: `scripts/setup.py`

- [ ] **Step 1: Create the `scripts/` directory and scaffold the file**

Run:

```bash
mkdir -p scripts
```

Create `scripts/setup.py`:

```python
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
```

Make it executable:

```bash
chmod +x scripts/setup.py
```

- [ ] **Step 2: Smoke test `--help`**

Run:

```bash
python scripts/setup.py --help
```

Expected: argparse help with the three flags.

- [ ] **Step 3: Smoke test the scaffold**

Run:

```bash
python scripts/setup.py
```

Expected: prints `Setup wizard scaffold OK` plus the three resolved paths.

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.py
git commit -m "feat(setup): add setup wizard scaffold with dep check"
```

---

### Task 3: Env file I/O (TDD)

**Files:**
- Modify: `scripts/setup.py`
- Create: `tests/test_setup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_setup.py`:

```python
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
```

Also create `scripts/__init__.py` (empty) so pytest can import `scripts.setup` as a package:

```bash
touch scripts/__init__.py
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: FAIL — `AttributeError: module 'scripts.setup' has no attribute 'load_env'` (and similar for `save_env`).

- [ ] **Step 3: Implement `load_env` and `save_env`**

Edit `scripts/setup.py`. Add these functions above `_check_optional_deps`:

```python
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
```

Add `import os` to the imports at the top.

- [ ] **Step 4: Run the tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/setup.py tests/test_setup.py
git commit -m "feat(setup): env file load/save with 0o600 perms"
```

---

### Task 4: Provider metadata + value classification (TDD)

**Files:**
- Modify: `scripts/setup.py`
- Modify: `tests/test_setup.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_setup.py`:

```python
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
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: FAIL on the new tests with `AttributeError: module 'scripts.setup' has no attribute 'PROVIDERS'`.

- [ ] **Step 3: Implement provider table + classification**

In `scripts/setup.py`, add below `save_env`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup.py tests/test_setup.py
git commit -m "feat(setup): provider metadata + secret/plaintext classifier"
```

---

### Task 5: docker-compose renderer (TDD)

**Files:**
- Modify: `scripts/setup.py`
- Modify: `tests/test_setup.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_setup.py`:

```python
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
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: FAIL with `AttributeError: module 'scripts.setup' has no attribute 'render_compose'`.

- [ ] **Step 3: Implement `render_compose`**

In `scripts/setup.py`, add below the classification helpers:

```python
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
```

- [ ] **Step 4: Run the tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup.py tests/test_setup.py
git commit -m "feat(setup): docker-compose renderer"
```

---

### Task 6: Nix module renderer (TDD)

**Files:**
- Modify: `scripts/setup.py`
- Modify: `tests/test_setup.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_setup.py`:

```python
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
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: FAIL — `render_nix_module` doesn't exist yet.

- [ ] **Step 3: Implement `render_nix_module`**

In `scripts/setup.py`, add below `render_compose`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup.py tests/test_setup.py
git commit -m "feat(setup): NixOS module renderer (flake + non-flake variants)"
```

---

### Task 7: Core step (JWT secret + APP_BASE_URL)

**Files:**
- Modify: `scripts/setup.py`

This step is UI-interactive; no unit test (manually verified at the end).

- [ ] **Step 1: Add `step_core`**

In `scripts/setup.py`, add (above `main`):

```python
import secrets as _secrets


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
```

- [ ] **Step 2: Manual smoke**

Run (interactive — press Enter twice to accept defaults):

```bash
python scripts/setup.py
```

This currently still uses the scaffold `main()`, so step_core won't fire yet — defer real testing to Task 11. Just make sure the file still parses:

```bash
python -c "import scripts.setup; print('ok')"
```

Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/setup.py
git commit -m "feat(setup): core step — JWT generation + APP_BASE_URL prompt"
```

---

### Task 8: OIDC providers step

**Files:**
- Modify: `scripts/setup.py`

- [ ] **Step 1: Add `step_oidc`**

In `scripts/setup.py`, append (above `main`):

```python
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
```

- [ ] **Step 2: Sanity-check module still imports**

Run:

```bash
python -c "import scripts.setup; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Run unit tests (they should still pass)**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.py
git commit -m "feat(setup): OIDC providers step (add/edit/remove loop)"
```

---

### Task 9: SMTP step + output step + wire `main()`

**Files:**
- Modify: `scripts/setup.py`

- [ ] **Step 1: Add `step_smtp`**

In `scripts/setup.py`, append (above `main`):

```python
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
```

- [ ] **Step 2: Add `step_output`**

Append in `scripts/setup.py`:

```python
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
```

- [ ] **Step 3: Wire `main()`**

Replace the existing `main()` body in `scripts/setup.py` with:

```python
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
```

- [ ] **Step 4: Run unit tests**

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: all PASS.

- [ ] **Step 5: Add a test for `_split_secrets` so we don't regress later**

Append to `tests/test_setup.py`:

```python
def test_split_secrets_separates_keys_correctly() -> None:
    env = {
        "SKYNET_JWT_SECRET_KEY": "x",
        "SKYNET_APP_BASE_URL": "https://example.org",
        "SKYNET_AUTH_GITHUB_CLIENT_ID": "id",
        "SKYNET_AUTH_GITHUB_CLIENT_SECRET": "sec",
        "SKYNET_SMTP_PASSWORD": "pw",
        "SKYNET_SMTP_HOST": "smtp.example.com",
        "HOME": "/should/be/ignored",
    }
    secret, plaintext = wizard._split_secrets(env)
    assert set(secret) == {"SKYNET_JWT_SECRET_KEY",
                           "SKYNET_AUTH_GITHUB_CLIENT_SECRET",
                           "SKYNET_SMTP_PASSWORD"}
    assert set(plaintext) == {"SKYNET_APP_BASE_URL",
                              "SKYNET_AUTH_GITHUB_CLIENT_ID",
                              "SKYNET_SMTP_HOST"}
    assert "HOME" not in secret and "HOME" not in plaintext
```

Run:

```bash
pytest tests/test_setup.py -v
```

Expected: all PASS, including the new test.

- [ ] **Step 6: Commit**

```bash
git add scripts/setup.py tests/test_setup.py
git commit -m "feat(setup): SMTP step + output step + main() wiring"
```

---

### Task 10: README + dev docs update

**Files:**
- Modify: `README.md`
- Modify: `docs/development.md`

- [ ] **Step 1: Add a wizard section to the README Quick start**

In `README.md`, find this block (around line 15-25):

```markdown
1. Create an env file with secrets (see [docs/deployment/secrets.md](docs/deployment/secrets.md) and [docs/deployment/oidc-providers.md](docs/deployment/oidc-providers.md) for the full list):

```bash
cat > skynetcontrol.env <<'EOF'
SKYNET_JWT_SECRET_KEY=replace-with-openssl-rand-hex-32
SKYNET_APP_BASE_URL=https://net.example.org
SKYNET_AUTH_GITHUB_ENABLED=true
SKYNET_AUTH_GITHUB_CLIENT_ID=Iv1.xxxxxxxx
SKYNET_AUTH_GITHUB_CLIENT_SECRET=xxxxxxxx
EOF
```
```

Replace it with:

```markdown
1. Create an env file with secrets. The fastest way is the interactive setup wizard, which can also generate your `docker-compose.yml` (or a NixOS module snippet):

```bash
pip install -e ".[setup]"
python scripts/setup.py
```

The wizard walks you through JWT secret generation, OIDC provider credentials, optional SMTP, and the deployment artifact format. See [docs/deployment/secrets.md](docs/deployment/secrets.md) and [docs/deployment/oidc-providers.md](docs/deployment/oidc-providers.md) for the full list of variables if you prefer to write the file by hand:

```bash
cat > skynetcontrol.env <<'EOF'
SKYNET_JWT_SECRET_KEY=replace-with-openssl-rand-hex-32
SKYNET_APP_BASE_URL=https://net.example.org
SKYNET_AUTH_GITHUB_ENABLED=true
SKYNET_AUTH_GITHUB_CLIENT_ID=Iv1.xxxxxxxx
SKYNET_AUTH_GITHUB_CLIENT_SECRET=xxxxxxxx
EOF
```
```

- [ ] **Step 2: Mention the wizard in `docs/development.md`**

Open `docs/development.md`. Add a short subsection near the top (before the dev-shell instructions or wherever the early "first time setup" copy lives):

```markdown
### Setup wizard

For a fresh deployment (your own or a friend's), `python scripts/setup.py` is an
interactive prompt_toolkit wizard that produces `skynetcontrol.env` plus a
`docker-compose.yml` or `skynetcontrol.nix` module snippet. It pre-fills from any
existing env file so re-running it to edit a single provider is safe.
```

(If the doc has obvious section headings, slot it under a "First-time setup" or "Configuration" heading rather than at the very top.)

- [ ] **Step 3: Commit**

```bash
git add README.md docs/development.md
git commit -m "docs: point new operators at the setup wizard"
```

---

### Task 11: End-to-end manual smoke

**Files:** none modified — verification only.

- [ ] **Step 1: Run the wizard in a scratch dir, docker-compose path**

```bash
mkdir -p /tmp/skynet-setup-smoke && cd /tmp/skynet-setup-smoke
python /home/ku0hn/dev/SkyNetControl/scripts/setup.py
```

At the prompts:
- `APP_BASE_URL`: press Enter (default)
- OIDC: `a` → pick GitHub → `Client ID`: `test-id` → `Client secret`: `test-secret` → `d`
- SMTP: `n`
- Output: `1` → port Enter → volume Enter

Expected files in `/tmp/skynet-setup-smoke`:
- `skynetcontrol.env` (mode `-rw-------`, contains JWT + APP_BASE_URL + 3 GitHub keys)
- `docker-compose.yml` referencing `./skynetcontrol.env`

Verify:

```bash
ls -l skynetcontrol.env docker-compose.yml
grep SKYNET_ skynetcontrol.env
grep skynetcontrol.env docker-compose.yml
cd - && rm -rf /tmp/skynet-setup-smoke
```

Expected: mode `0600` on env file; both files present; compose references env file.

- [ ] **Step 2: Run the wizard, NixOS flake path**

```bash
mkdir -p /tmp/skynet-setup-smoke && cd /tmp/skynet-setup-smoke
python /home/ku0hn/dev/SkyNetControl/scripts/setup.py
```

- `APP_BASE_URL`: `https://net.example.org`
- OIDC: `a` → pick GitHub → `Client ID`: `Iv1.test` → `Client secret`: `ghs_test` → `d`
- SMTP: `n`
- Output: `2` → EnvironmentFile path: press Enter

Verify:

```bash
ls -l skynetcontrol.env skynetcontrol.nix
grep -E "(JWT_SECRET|CLIENT_SECRET)" skynetcontrol.env   # should match
grep -E "(JWT_SECRET|CLIENT_SECRET)" skynetcontrol.nix   # should NOT match
grep "APP_BASE_URL" skynetcontrol.nix                     # should match
grep "inputs.skynetcontrol" skynetcontrol.nix             # should match
cd - && rm -rf /tmp/skynet-setup-smoke
```

Expected:
- env file has secrets only.
- Nix file has plaintext settings but no secret values.
- Flake-style import line is present.

- [ ] **Step 3: Run the wizard, NixOS non-flake path + re-run pre-fill**

Same as Step 2 but pick output `3`. After it finishes, run the wizard again in the same directory:

```bash
python /home/ku0hn/dev/SkyNetControl/scripts/setup.py
```

Expected:
- The opening banner says "Existing env loaded from skynetcontrol.env".
- `APP_BASE_URL` prompt shows `[https://net.example.org]` as the default.
- OIDC step shows "Currently enabled: GitHub".
- Output step writes `skynetcontrol.generated.nix` (because `skynetcontrol.nix` already exists) and prints the `mv` reminder.

```bash
ls skynetcontrol.generated.nix
cd - && rm -rf /tmp/skynet-setup-smoke
```

- [ ] **Step 4: Final unit test sweep**

```bash
pytest tests/test_setup.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Wrap-up commit (if anything was missed)**

If the smoke tests uncovered any issue and you fixed it, commit the fix here. Otherwise nothing to commit — the wizard is done.

```bash
git status
```

Expected: clean working tree.

---

## Done

After Task 11 the wizard is feature-complete. Operators can run `python scripts/setup.py` from a fresh clone (after `pip install -e ".[setup]"`) and walk away with a deployable env file plus their chosen artifact.
