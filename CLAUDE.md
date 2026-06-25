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
| Run setup wizard | Browser at `http://localhost:5173/setup` (first-boot only; reset with `rm skynetcontrol.db` then restart) |
| Mint admin recovery token | `.venv/bin/skynetcontrol-recovery mint-admin-token` |

## Coding conventions

- **Commits**: Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`). Scope when it clarifies (`feat(setup):`, `fix(ci):`).
- **Ruff**: line-length 120, `select = ["E", "F"]`. Tests have permissive per-file ignores; production code does not. CI runs `ruff check` and will fail the build — match it locally before pushing.
- **Config is unified** across DB-via-AppConfig (everything operator/admin-tunable: OAuth providers, SMTP, net basics, scanner, callbook, delivery routing) and env (bootstrap-only: `SKYNET_DATABASE_URL`, `SKYNET_JWT_SECRET_KEY`, `SKYNET_APP_BASE_URL`, plus `SKYNET_DEBUG` / `SKYNET_STATIC_DIR`). The first-boot web wizard at `/setup` and the recovery wizard (`skynetcontrol-recovery mint-admin-token` → `/recovery` → recovery-mode wizard) own everything else.
- **Pydantic-settings tests**: `Settings()` reads `os.environ` at construction time. Tests that exercise env-derived fields must `monkeypatch.delenv` / `setenv` to isolate from the host environment.
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

## Tool failure handling

The harness does NOT auto-retry failed tool results. If a tool returns `[Tool result missing due to internal error]`, an empty result, a transport error, or `"tool use rejected"` **when the user wasn't actually prompted** (a silent reject is a harness failure, not a real denial), you must react **in the same turn** — retry immediately or surface the failure as text. Do not end the turn asking the human whether to retry; that just re-creates the stall you're trying to prevent. If the user genuinely denied it, they'll tell you on the next turn.

If your turn ends without a follow-up after a tool failure, the session goes silent and waits for the human (no inference, no progress). This has stalled multi-day SDD runs more than once.

Corollary: don't batch a long-running or flaky Bash call (e.g. `review-package` on a large diff, or a long subagent dispatch) in parallel with unrelated tool calls. A transport failure on the slow one gets buried among the successes. Run it on its own so the error is impossible to miss.

## Permissions

The user prefers commands that don't trigger permission prompts. When a dedicated tool can do the job, use it instead of Bash:

- File reads → `Read`, not `cat`/`head`/`tail`/`sed`.
- File edits → `Edit`/`Write`, not `sed`/`awk`/heredoc.
- Search → `Bash` with `grep`/`find` (already allowed) before agents.

`git`, `pytest`, `nix-shell`, `gh`, `ruff` are routine — use them freely via Bash. But e.g. `curl` to untrusted URLs, network installs outside the venv, or anything touching `~/.config` outside `.claude/` warrants pausing.
