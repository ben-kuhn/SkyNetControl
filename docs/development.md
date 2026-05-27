# Development setup

This guide walks through standing up SkyNetControl locally for development or evaluation.

## Prerequisites

- **Nix** (single-user or multi-user). The repo ships a `shell.nix` that installs every other dependency.
- An OAuth app on a provider you have access to (GitHub is the easiest — no domain verification needed). See **OAuth setup** below.
- Optional: an Anthropic API key (for the activity-brainstorm chat). Not required to boot.
- Optional: a PAT mailbox directory (for end-to-end check-in scan testing). Not required to boot.

If you're not on Nix, you can install the deps manually — Python 3.12, Node.js 22, npm — and skip the `nix-shell` step.

## First-time setup

### 1. Enter the dev shell

```bash
nix-shell
```

This creates `.venv`, installs Python deps via `pip install -e ".[dev]"`, and runs `npm install` in `frontend/`. You should see `SkyNetControl dev environment ready.`

(Without Nix: create a venv yourself, `pip install -e ".[dev]"`, and `cd frontend && npm install`.)

### 2. Configure environment variables

```bash
cp .env.example .env
$EDITOR .env
```

At a minimum, set:

- `SKYNET_JWT_SECRET_KEY` — generate with `openssl rand -hex 32`
- `SKYNET_APP_BASE_URL` — keep `http://localhost:5173` for local dev (see explanation below)
- One auth provider enabled with real OAuth credentials (see next step)

Load the env vars into your shell:

```bash
set -a; source .env; set +a
```

Or use [direnv](https://direnv.net/), or pass them via your runner of choice.

### 3. Register an OAuth app

Pick whichever provider you prefer. For local dev, GitHub is simplest:

1. <https://github.com/settings/developers> → **New OAuth App**.
2. Application name: anything (e.g., `SkyNetControl dev`).
3. Homepage URL: `http://localhost:5173`.
4. Authorization callback URL: **`http://localhost:5173/api/auth/callback/github`**.
5. Copy the Client ID; generate and copy a new Client Secret.
6. Set in `.env`:
   ```
   SKYNET_AUTH_GITHUB_ENABLED=true
   SKYNET_AUTH_GITHUB_CLIENT_ID=Iv1.abcd...
   SKYNET_AUTH_GITHUB_CLIENT_SECRET=xxxxxxxxxx
   ```
7. Re-source the env (`set -a; source .env; set +a`).

For other providers, see [docs/deployment/secrets.md](deployment/secrets.md) and the developer console links there.

#### Why `SKYNET_APP_BASE_URL=http://localhost:5173` in dev?

The backend runs on `:8000` and the frontend on `:5173`. The Vite dev server proxies `/api/*` to the backend. The OAuth callback handler:

1. Receives the OAuth provider's redirect at `{APP_BASE_URL}/api/auth/callback/{provider}`.
2. Sets the `access_token` cookie on the response.
3. Redirects the browser to `APP_BASE_URL`.

For the SPA to see the cookie on subsequent API requests, the callback and the SPA must be on the same origin. Setting `APP_BASE_URL` to the Vite port routes the OAuth callback through the proxy, so the cookie binds to `:5173` where the SPA lives.

In production, set `SKYNET_APP_BASE_URL` to your real `https://` URL — backend and frontend are served by the same Uvicorn process so origin issues don't apply.

### 4. Create the database

```bash
alembic upgrade head
```

This creates `skynetcontrol.db` (or migrates a PostgreSQL DB if `SKYNET_DATABASE_URL` points at one) with all current tables.

### 5. Start the servers

In two terminals (or one with `&` and a process manager of your choice):

```bash
# Terminal 1: backend
uvicorn backend.app:create_app --factory --reload --host 127.0.0.1 --port 8000
```

```bash
# Terminal 2: frontend
cd frontend && npm run dev
```

Visit <http://localhost:5173>.

### 6. First login → admin

Click **Sign in**, complete the OAuth flow. The first user to sign in is automatically granted the `ADMIN` role. Subsequent users land in `PENDING` and must be approved by an admin via `/users`.

After signing in, the app prompts you to register your callsign — enter your ham callsign (it becomes your primary key in the system).

### 7. Configure runtime settings

Visit `/config` (admin-only) to set the operational settings. The interesting ones:

- `net_address` — your Winlink net address (e.g., `w0ne@winlink.org`).
- `pat_mailbox_path` — filesystem path to PAT's mailbox directory for check-in scans.
- `claude_api_key` — Anthropic API key for the activity-brainstorm chat.
- `delivery.backends` — JSON list of enabled delivery backends, e.g., `["email"]`.
- `delivery.email.to_address` — group address for sending reminders/rosters via email.

For the full list, see [docs/deployment/app-config-keys.md](deployment/app-config-keys.md).

## Common dev tasks

### Run the backend test suite

```bash
python -m pytest tests/ -q
```

Or a focused subset:

```bash
python -m pytest tests/test_checkin_routes.py -v
```

### Type-check the frontend

```bash
cd frontend && npx tsc --noEmit
```

### Build the frontend for production

```bash
cd frontend && npm run build
```

Output lands in `frontend/dist/`. The backend serves it at `/` when `SKYNET_STATIC_DIR` points there (default).

### Generate a new Alembic migration

```bash
alembic revision --autogenerate -m "describe the change"
```

Inspect the generated file, trim any spurious operations, then `alembic upgrade head`.

### Reset the database

```bash
rm skynetcontrol.db
alembic upgrade head
```

You'll need to sign in again — the first user becomes admin all over again.

## Troubleshooting

### "No auth providers are enabled" at startup

You don't have a `SKYNET_AUTH_*_ENABLED=true` env var with matching `CLIENT_ID` and `CLIENT_SECRET` values. Pick a provider, register an OAuth app, and set the three vars.

### OAuth redirects to localhost:8000 instead of staying on 5173

Your OAuth app's callback URL is registered as `http://localhost:8000/...`. Either update it to `http://localhost:5173/...` (recommended) or set `SKYNET_APP_BASE_URL=http://localhost:8000` to match. Note that the latter requires you to navigate to `:8000` directly — the SPA proxy won't carry the cookie.

### Frontend can't reach `/api/*`

Vite proxy config is in `frontend/vite.config.ts` — it forwards `/api/*` to `localhost:8000`. If you changed the backend port, update the proxy target there too.

### "Claude API key not configured" on the activities page

Set `claude_api_key` in the in-app `/config` page (this is a runtime AppConfig value, not an env var). The chat features are gracefully disabled until it's set.

### Tests fail with `ImportError` after pulling new commits

The dev shell installs the package in editable mode (`pip install -e`). If new dependencies were added, re-enter the shell or run `pip install -e ".[dev]"` again.

## Production deployment

See [docs/deployment/secrets.md](deployment/secrets.md) for env var management patterns (sops-nix, agenix, systemd EnvironmentFile, Docker env-file). The NixOS module in `module.nix` handles `ExecStartPre` migrations automatically.
