# CI/CD Pipeline — Design Spec

**Goal:** Automate testing, linting, Nix build verification, and OCI container publishing via GitHub Actions.

**Architecture:** Two workflows — `ci.yml` (test + lint + build on every push/PR) and `container.yml` (build and push OCI image on push to main). All Nix-based using DeterminateSystems actions.

**Tech Stack:** GitHub Actions, Nix, Ruff, pytest, `dockerTools.buildLayeredImage`

---

## CI Workflow (`.github/workflows/ci.yml`)

**Trigger:** `push` and `pull_request` to `main`

Three parallel jobs, all on `ubuntu-latest` with shared Nix setup:
- DeterminateSystems/nix-installer-action
- DeterminateSystems/magic-nix-cache-action

### Job: test

Runs the full Python test suite via the existing dev shell:

```
nix-shell --run "python -m pytest tests/ -v"
```

### Job: lint

Runs Ruff linting and format checking:

```
nix-shell --run "ruff check backend/ tests/"
nix-shell --run "ruff format --check backend/ tests/"
```

### Job: build

Verifies the Nix package builds:

```
nix-build default.nix
```

---

## Container Workflow (`.github/workflows/container.yml`)

**Trigger:** `push` to `main` only

### Job: build-and-push

1. Install Nix + Magic Nix Cache
2. `nix-build oci.nix` — produces Docker image tarball
3. `docker load < result` — loads image into Docker
4. Tag as `ghcr.io/<owner>/skynetcontrol:latest`
5. `docker push` to GitHub Container Registry

**Auth:** Built-in `GITHUB_TOKEN` with `packages: write` permission. No manual secret configuration needed.

**Image tag:** `latest` only (no version tags).

---

## Ruff Configuration

Added to `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py312"
line-length = 120

[tool.ruff.lint]
select = ["E", "F"]
```

- `E` — pycodestyle errors
- `F` — pyflakes errors
- Line length 120 matches existing code style
- No aggressive rules — catches real errors without being noisy

Ruff added to `[project.optional-dependencies] dev`.

---

## Frontend Stub (`frontend.nix`)

`default.nix` imports `frontend.nix` to copy built frontend assets. Since the frontend doesn't exist yet, `frontend.nix` produces a minimal derivation with a placeholder `index.html`. This will be replaced when the frontend is built.

---

## Shell Changes (`shell.nix`)

Add `pkgs.ruff` to `buildInputs` so `ruff` is available in the dev shell.

---

## File Summary

| File | Action | Description |
|------|--------|-------------|
| `.github/workflows/ci.yml` | Create | Test, lint, build jobs |
| `.github/workflows/container.yml` | Create | Build and push OCI image to ghcr.io |
| `frontend.nix` | Create | Stub derivation with placeholder index.html |
| `pyproject.toml` | Modify | Add ruff to dev deps, add `[tool.ruff]` config |
| `shell.nix` | Modify | Add ruff to buildInputs |

---

## What This Phase Does NOT Include

- **Version-tagged container images** — only `latest` for now; add tag-based builds when release workflow is needed
- **Deployment automation** — pipeline builds and pushes images but does not deploy to any environment
- **Secrets management** — OIDC/JWT secrets handled separately (future sops-nix/agenix integration)
- **Frontend build step** — `frontend.nix` is a stub; real frontend build will replace it
- **Cachix or other Nix binary cache** — Magic Nix Cache handles CI caching; no external cache service
