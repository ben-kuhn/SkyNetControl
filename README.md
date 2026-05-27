# SkyNetControl

A web application for managing a weekly Winlink net — schedule, activity planning, participant reminders, check-in tracking, roster generation, and long-term participation records.

Built for ham radio operators running recurring digital nets, with first-class Winlink (PAT mailbox) integration, groups.io / email / Winlink delivery backends, and Claude-powered activity brainstorming.

## Quick start

```bash
# Drop into a dev shell with Python + Node + everything installed.
nix-shell

# Configure auth (see docs/development.md for OAuth setup).
cp .env.example .env
$EDITOR .env

# Create the SQLite database.
alembic upgrade head

# Run the backend (http://localhost:8000).
uvicorn backend.app:create_app --factory --reload &

# Run the frontend (http://localhost:5173).
cd frontend && npm run dev
```

Visit <http://localhost:5173>, sign in via your configured OAuth provider, and the first user becomes admin automatically.

For the full walkthrough — including how to register an OAuth app, run migrations, and set up runtime config — see **[docs/development.md](docs/development.md)**.

## Tech stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, React Router
- **Database**: SQLite (default) or PostgreSQL via `SKYNET_DATABASE_URL`
- **Packaging**: Nix (overlay, NixOS module, OCI image — no Dockerfile)
- **Auth**: OAuth 2.0 / OIDC — Google, Microsoft, GitHub, Discord, Facebook, generic OIDC

## Documentation

| Doc | Audience |
|-----|----------|
| [docs/development.md](docs/development.md) | Local development setup |
| [docs/deployment/secrets.md](docs/deployment/secrets.md) | Environment variables and secret management |
| [docs/deployment/app-config-keys.md](docs/deployment/app-config-keys.md) | DB-stored runtime configuration |
| [docs/superpowers/specs/2026-04-16-winlink-net-manager-design.md](docs/superpowers/specs/2026-04-16-winlink-net-manager-design.md) | Product design spec (architecture, modules, data flow) |

## License

TBD.
