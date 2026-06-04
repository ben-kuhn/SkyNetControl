# Setup Wizard — Design Spec

**Goal:** Provide an interactive setup wizard, `scripts/setup.py`, that walks a new operator through producing a ready-to-deploy SkyNetControl configuration. Inspired by PacketQTH's `tools/configure.py`.

**Outputs:** A `skynetcontrol.env` file plus one of three deployment artifacts chosen by the user: `docker-compose.yml`, a NixOS module snippet for a flake-based config, or a NixOS module snippet for a non-flake config.

**Tech stack:** Python 3.12, `prompt_toolkit` and `pyyaml` (both new, added to a `setup` optional-dependency extra), `secrets` from the stdlib.

---

## Invocation

- Location: `scripts/setup.py` (new top-level `scripts/` directory).
- Runs as `python scripts/setup.py` from the repo root.
- CLI flags:
  - `--env-file PATH` (default `./skynetcontrol.env`)
  - `--compose-file PATH` (default `./docker-compose.yml`)
  - `--nix-file PATH` (default `./skynetcontrol.nix`)
- Imports `prompt_toolkit`; if missing, prints `pip install -e ".[setup]"` and exits non-zero.

## Re-run behavior

- On launch, loads existing `skynetcontrol.env` if present and uses its values as defaults.
- Empty input at any prompt keeps the existing value.
- Secrets are masked in default hints (e.g. `***abcd12`); typing nothing keeps them.
- Provider step shows currently enabled providers and offers add / edit / remove / done.

---

## Steps

### Step 1: Core

- Generate `SKYNET_JWT_SECRET_KEY` via `secrets.token_hex(32)` if not already in the env. Reuse the existing value on re-run.
- Prompt `SKYNET_APP_BASE_URL` (default `http://localhost:8000`). Strip trailing slash.

### Step 2: OIDC providers (loop)

Menu actions: **add provider / edit existing / remove existing / done**.

Supported providers (matches `docs/deployment/oidc-providers.md`):

| Provider | Env prefix | Extra fields |
|----------|-----------|--------------|
| Google | `SKYNET_AUTH_GOOGLE_` | — |
| GitHub | `SKYNET_AUTH_GITHUB_` | — |
| Microsoft | `SKYNET_AUTH_MICROSOFT_` | — |
| Discord | `SKYNET_AUTH_DISCORD_` | — |
| Facebook | `SKYNET_AUTH_FACEBOOK_` | — |
| Generic OIDC | `SKYNET_AUTH_OIDC_` | `ISSUER_URL` (required) |

Per provider:
- Prompt `CLIENT_ID`, `CLIENT_SECRET` (masked).
- For generic OIDC, also `ISSUER_URL`.
- Set `{PREFIX}ENABLED=true`.
- Print a link to that provider's developer console.

No connection test — OAuth requires a full browser flow.

### Step 3: SMTP

Yes/no to enable. If yes, prompt:
- `SKYNET_SMTP_HOST`
- `SKYNET_SMTP_PORT` (default `587`)
- `SKYNET_SMTP_USERNAME`
- `SKYNET_SMTP_PASSWORD` (masked)
- `SKYNET_SMTP_FROM_ADDRESS`
- `SKYNET_SMTP_USE_TLS` (default `true`)

Skipping leaves these unset; email is silently disabled per existing behavior.

### Step 4: Output format

Prompt for the deployment target:

```
1) docker-compose.yml
2) NixOS module (with flakes)
3) NixOS module (without flakes)
```

Behavior differs only at write time (see below). Same collected values feed all three.

---

## Value classification

The collected values split into two categories for output:

- **Plaintext** (safe inline in Nix): `APP_BASE_URL`, `AUTH_*_ENABLED`, `AUTH_*_CLIENT_ID`, `AUTH_OIDC_ISSUER_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_FROM_ADDRESS`, `SMTP_USE_TLS`.
- **Secret** (env file only): `JWT_SECRET_KEY`, `AUTH_*_CLIENT_SECRET`, `SMTP_PASSWORD`.

`docker-compose` writes every collected key into `skynetcontrol.env`. The Nix outputs write only secrets to `skynetcontrol.env` and put plaintext keys inline in the module snippet.

---

## Output files

### 1. docker-compose

- `skynetcontrol.env` — all collected `SKYNET_*` keys, mode `0600`.
- `docker-compose.yml` — references the env file:
  ```yaml
  services:
    skynetcontrol:
      image: ghcr.io/ben-kuhn/skynetcontrol:latest
      restart: unless-stopped
      ports: ["{port}:8000"]
      volumes: ["{volume}:/data"]
      env_file: ["./skynetcontrol.env"]
  volumes:
    {volume}:
  ```
- Additional prompts: host port (default `8000`), data volume name (default `skynetcontrol-data`).
- If `docker-compose.yml` already exists, write to `docker-compose.generated.yml` and tell the user to `mv` it.

### 2. NixOS with flakes

- `skynetcontrol.env` — secrets only, mode `0600`.
- `skynetcontrol.nix` — snippet ready to paste into a flake-based config:
  ```nix
  { inputs, ... }: {
    imports = [ (import "${inputs.skynetcontrol}/module.nix") ];

    services.skynetcontrol = {
      enable = true;
      settings = {
        APP_BASE_URL = "...";
        AUTH_GITHUB_ENABLED = "true";
        AUTH_GITHUB_CLIENT_ID = "...";
        # ...
      };
    };

    systemd.services.skynetcontrol.serviceConfig.EnvironmentFile = [
      "/run/skynetcontrol/env"
    ];
  }
  ```
- After writing, print a reminder to add
  `skynetcontrol.url = "github:ben-kuhn/SkyNetControl";`
  to the flake inputs.

### 3. NixOS without flakes

- `skynetcontrol.env` — same secrets-only file as the flake case.
- `skynetcontrol.nix` — same `services.skynetcontrol` block, but the import line becomes:
  ```nix
  imports = [ /etc/nixos/skynetcontrol/module.nix ];
  ```
  with a printed reminder to `git clone https://github.com/ben-kuhn/SkyNetControl /etc/nixos/skynetcontrol`.

---

## Module layout

Single file: `scripts/setup.py`, ~350 lines. Sections (matches PacketQTH's `configure.py` layout):

1. Env file I/O — `load_env`, `save_env` (mode `0600`, replaces full file).
2. Provider metadata table — list of dicts with name, prefix, docs URL, extra fields.
3. `step_core(env)`, `step_oidc(env)`, `step_smtp(env)`, `step_output(env, paths)`.
4. Output renderers — `render_compose(env, host_port, volume)`, `render_nix_module(env, *, flakes: bool)`.
5. `main()` — argparse, load env, run steps, print summary.

---

## Dependency wiring

- Add to `pyproject.toml`:
  ```toml
  [project.optional-dependencies]
  setup = ["prompt_toolkit>=3.0", "pyyaml>=6.0"]
  ```
- Update `shell.nix` to install `.[dev,setup]` so nix-shell users get it automatically.
- If `prompt_toolkit` import fails, the script prints
  `pip install -e ".[setup]"`
  and exits 1.

---

## Testing

`tests/test_setup.py`:

- Env file round-trip: write/load preserves keys and applies mode `0600`.
- Value classification: secret keys never leak into the Nix module renderer output; plaintext keys never appear in the secrets-only env file.
- Provider metadata covers all six providers and matches the prefixes in `docs/deployment/oidc-providers.md`.
- `render_compose` produces YAML that loads with `yaml.safe_load` and contains `env_file: ./skynetcontrol.env`.
- `render_nix_module` (both flake and non-flake variants) contains the expected import line and a `services.skynetcontrol.settings` block with the plaintext keys.

UI flow / prompt_toolkit prompts are not unit tested.

---

## Documentation updates

- Add a "Setup wizard" subsection to the README's Quick start, before the manual `cat > skynetcontrol.env` example: `python scripts/setup.py` as the recommended path.
- Note in `docs/development.md` that the wizard exists for new deployments.
