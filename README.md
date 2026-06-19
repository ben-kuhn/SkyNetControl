# SkyNetControl

A web application for managing a weekly Winlink net — schedule, activity planning, participant reminders, check-in tracking, roster generation, and long-term participation records.

Built for ham radio operators running recurring digital nets, with first-class Winlink (PAT mailbox) integration, groups.io / email / Winlink delivery backends, and Claude-powered activity brainstorming.

## Quick start

### Production (Docker Compose)

A reproducible OCI image is published on every push to `main`:

**`ghcr.io/ben-kuhn/skynetcontrol:latest`**

1. Create an env file with the three bootstrap secrets. Everything else (OAuth providers, SMTP, net basics) is configured through the first-boot wizard at `/setup` after the container is running.

```bash
cat > skynetcontrol.env <<EOF
SKYNET_DATABASE_URL=sqlite:////data/skynetcontrol.db
SKYNET_JWT_SECRET_KEY=$(openssl rand -hex 32)
SKYNET_APP_BASE_URL=https://net.example.org

# Optional but recommended behind a reverse proxy (Cloudflare, nginx, etc.):
# without this, the per-IP rate limiter buckets every visitor under the
# proxy's IP. See docs/deployment/operations.md for details.
# SKYNET_TRUSTED_PROXIES=127.0.0.1

# Optional: separate AES key for at-rest credential encryption. Skip to
# reuse the JWT secret. Set this to rotate the two independently.
# SKYNET_SECRETS_KEY=$(openssl rand -hex 32)
EOF
```

2. Create `docker-compose.yml`:

```yaml
services:
  skynetcontrol:
    image: ghcr.io/ben-kuhn/skynetcontrol:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - skynetcontrol-data:/data
    env_file:
      - skynetcontrol.env

volumes:
  skynetcontrol-data:
```

3. Start the server:

```bash
docker compose up -d
```

The container applies any pending database migrations on startup before
launching the server, so first-run setup and version upgrades both work
with a plain `docker compose up -d`. If you ever need to run alembic
manually (e.g. to inspect history or downgrade), the wrapper is on PATH:
`docker compose run --rm --entrypoint skynetcontrol-alembic skynetcontrol history`.

Front it with nginx / Caddy / Traefik for TLS. Visit your `APP_BASE_URL` — the first-boot wizard at `/setup` walks you through net basics, one OAuth provider (Step 2 shows the redirect URI to register), optional SMTP, and then a sign-in that creates the first admin user with the callsign you entered. After setup completes, the wizard route 410's and the app is fully usable.

For ongoing configuration (OAuth providers, SMTP, PAT mailbox, scanner, delivery backends, Claude API key…), use the `/config` page once you're signed in as admin. See **[docs/deployment/app-config-keys.md](docs/deployment/app-config-keys.md)** for the full list of AppConfig keys.

If you ever lock yourself out of OAuth, `docker compose exec skynetcontrol skynetcontrol-recovery mint-admin-token` prints a one-time URL that bypasses the broken auth so you can edit the OAuth settings via the `/setup` wizard in recovery mode.

### Production (NixOS)

For NixOS hosts, use the `services.skynetcontrol` module from `module.nix`. It handles state directories, the service user, automatic migrations, and systemd hardening:

```nix
services.skynetcontrol = {
  enable         = true;
  host           = "127.0.0.1";
  port           = 8040;
  stateDir       = "/storage/skynetcontrol";
  appBaseUrl     = "https://net.example.org";
  # One agenix / sops secret containing SKYNET_JWT_SECRET_KEY and
  # (optionally) SKYNET_SECRETS_KEY as an env-file. See secrets.md.
  secretsFile    = config.age.secrets.skynetcontrol.path;
  trustedProxies = "127.0.0.1";   # required behind a proxy
};
users.users.skynetcontrol.extraGroups = [ "pat" ];
```

#### Database storage

The module exposes `services.skynetcontrol.stateDir` (default `/var/lib/skynetcontrol`) and `services.skynetcontrol.databaseUrl` (default points at `<stateDir>/skynetcontrol.db`). Override either to put state on a dedicated ZFS dataset, a bind-mount, or external PostgreSQL. See **[docs/deployment/nix.md#custom-storage-location](docs/deployment/nix.md#custom-storage-location)** for ZFS / bind-mount / PostgreSQL recipes.

#### Backups and migration

Pick the backup pattern that matches your storage: ZFS snapshots, online `sqlite3 .backup` via systemd timer, restic over the state dir, or `pg_dump` for PostgreSQL. See **[docs/deployment/nix.md#backups](docs/deployment/nix.md#backups)** for the full recipes.

To move a database between engines (SQLite ↔ PostgreSQL) or between hosts, use the bundled `skynetcontrol-db-copy` CLI:

```bash
sudo skynetcontrol-alembic upgrade head  # against the new URL
sudo skynetcontrol-db-copy --replace \
  sqlite:////var/lib/skynetcontrol/skynetcontrol.db \
  'postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql'
```

See **[docs/deployment/nix.md#moving-between-database-backends](docs/deployment/nix.md#moving-between-database-backends)** for the full migration recipe.

### Local development

Hacking on the code? See **[docs/development.md](docs/development.md)** for the dev shell, OAuth setup tips, migrations, and starting both backend and frontend with hot reload.

## Tech stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, React Router
- **Database**: SQLite (default) or PostgreSQL via `SKYNET_DATABASE_URL`
- **Packaging**: Nix (overlay, NixOS module, OCI image — no Dockerfile)
- **Auth**: OAuth 2.0 / OIDC — GitHub, Google, Microsoft, Discord, Facebook, generic OIDC

## Documentation

| Doc | Audience |
|-----|----------|
| [docs/deployment/operations.md](docs/deployment/operations.md) | Day-to-day operations: first boot, recovery, key rotation, behind-proxy, backups |
| [docs/deployment/oidc-providers.md](docs/deployment/oidc-providers.md) | Register OAuth/OIDC apps for sign-in |
| [docs/deployment/secrets.md](docs/deployment/secrets.md) | Bootstrap env vars and at-rest encryption model |
| [docs/deployment/app-config-keys.md](docs/deployment/app-config-keys.md) | DB-stored runtime configuration (set via `/config`) |
| [docs/deployment/nix.md](docs/deployment/nix.md) | Production deployment via Nix (NixOS module, OCI image, overlay) |
| [docs/development.md](docs/development.md) | Local development setup |
| [docs/audit-2026-05-27.md](docs/audit-2026-05-27.md) | Most recent security + code audit |
| [docs/superpowers/specs/2026-04-16-winlink-net-manager-design.md](docs/superpowers/specs/2026-04-16-winlink-net-manager-design.md) | Product design spec (architecture, modules, data flow) |

## License

TBD.
