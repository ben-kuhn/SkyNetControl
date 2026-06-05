# SkyNetControl

Web app for managing a weekly Winlink ham-radio net. FastAPI backend + React/TypeScript frontend, SQLAlchemy (SQLite default, PostgreSQL supported), packaged via Nix overlay / NixOS module / OCI image (no Dockerfile, no flakes). Callsign is the natural primary key for users.

Spec: `docs/superpowers/specs/2026-04-16-winlink-net-manager-design.md`.
Feature designs land under `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` and implementation plans under `docs/superpowers/plans/`.

## Toolchain

- Host is **NixOS**. Node, Python, ruff, etc. are not on the system PATH. Use `nix-shell` (defined in `shell.nix`) for any toolchain command.
- The shell creates `.venv/` and pip-installs `.[dev]`. **Do not pip install into the host Python** — `pip install` only inside the venv.
- Nix garbage collection can break `.venv/bin/python` (it's a symlink into `/nix/store`). If you see "bad interpreter", `rm -rf .venv && nix-shell --run :` rebuilds it.
- Frontend tooling: `cd frontend && nix-shell -p nodejs_22 --run "npm <…>"`.

Common commands:

| Task | Command |
|------|---------|
| Backend tests | `.venv/bin/pytest -q` |
| Lint | `nix-shell --run "ruff check"` |
| Dev servers (backend + Vite) | `./run-dev.sh` |
| Frontend prod build | `cd frontend && nix-shell -p nodejs_22 --run "npm run build"` |
| Run wizard locally | `.venv/bin/skynetcontrol-setup` (after `pip install -e .` in the venv) |

## Coding conventions

- **Commits**: Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`). Scope when it clarifies (`feat(setup):`, `fix(ci):`).
- **Ruff**: line-length 120, `select = ["E", "F"]`. Tests have permissive per-file ignores; production code does not. CI runs `ruff check` and will fail the build — match it locally before pushing.
- **`prompt_toolkit` imports** in `backend/cli/setup.py` stay inside function bodies, not at module top, so the module is importable without that dep (tests rely on this).
- **Pydantic-settings tests**: `Settings()` reads `os.environ` at construction time. Tests that exercise env-derived fields must `monkeypatch.delenv` / `setenv` to isolate from the host environment. The `auth_oidc_providers` validator additionally treats an explicit `auth_oidc_providers=[…]` kwarg as authoritative (env scan skipped), so direct-construction tests are deterministic.
- **UI lists**: no pagination, no infinite scroll. Net data is small (hundreds of members, dozens of sessions); load all rows and use client-side filter/sort. Only add pagination if a dataset legitimately exceeds browser comfort.

## Workflow

For non-trivial work, use the superpowers skill chain: `brainstorming` → `writing-plans` → `subagent-driven-development` → `finishing-a-development-branch`. Each produces an artifact under `docs/superpowers/{specs,plans}/`. Trivial fixes (single-line bug, docs typo) can skip the spec/plan and go straight to a commit.

When dispatching implementation subagents:
- Use a worktree (`EnterWorktree`) so main stays clean.
- The worktree branches from `origin/main`; cherry-pick any unpushed local commits (spec + plan) into the worktree first.
- After all tasks complete, run `finishing-a-development-branch`. Default to merging back to `main` locally (solo project, no PR process needed unless something warrants review).
- Group bisectable changes into atomic commits. Unrelated cleanups go in their own commit even when discovered together.

## CI

After any push to `main`, **wait for CI green before starting the next task**. Run via `gh run list --branch main --limit 4 --json status,conclusion,name,headSha` (parse with `python3`, not `jq` — `jq` isn't installed). Surface failures and fix them before piling on more work. The workflows that matter are `CI` (pytest + ruff) and `Container` (OCI image build via Nix — failures here often mean `frontend.nix`'s `npmDepsHash` needs a refresh after a lockfile change).

Don't push without explicit user confirmation. Don't force-push to `main` ever without explicit ask.

## Permissions

The user prefers commands that don't trigger permission prompts. When a dedicated tool can do the job, use it instead of Bash:

- File reads → `Read`, not `cat`/`head`/`tail`/`sed`.
- File edits → `Edit`/`Write`, not `sed`/`awk`/heredoc.
- Search → `Bash` with `grep`/`find` (already allowed) before agents.

`git`, `pytest`, `nix-shell`, `gh`, `ruff` are routine — use them freely via Bash. But e.g. `curl` to untrusted URLs, network installs outside the venv, or anything touching `~/.config` outside `.claude/` warrants pausing.
