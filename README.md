# SkyNetControl

A web application for managing a weekly Winlink net — schedule, activity planning, participant reminders, check-in tracking, roster generation, and long-term participation records.

Built for ham radio operators running recurring digital nets, with first-class Winlink (PAT mailbox) integration, groups.io / email / Winlink delivery backends, and Claude-powered activity brainstorming.

## Quick start

### Production (Docker Compose)

A reproducible OCI image is published on every push to `main`:

**`ghcr.io/ben-kuhn/skynetcontrol:latest`**

1. Create an env file with secrets. The fastest way is the interactive setup wizard bundled in the image — it can also generate your `docker-compose.yml` (or a NixOS module snippet). Run it in the directory where you want the files to land:

```bash
docker run --rm -it -v "$PWD:/work" -w /work \
  ghcr.io/ben-kuhn/skynetcontrol:latest skynetcontrol-setup
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

Front it with nginx / Caddy / Traefik for TLS. Visit your `APP_BASE_URL`, sign in via the OAuth provider you configured, and the first user becomes admin automatically.

For configuration of OAuth providers (GitHub, Google, Microsoft, Authentik, Keycloak…), see **[docs/deployment/oidc-providers.md](docs/deployment/oidc-providers.md)**.

For the in-app runtime settings (net address, PAT mailbox path, Claude API key, delivery backends), see **[docs/deployment/app-config-keys.md](docs/deployment/app-config-keys.md)** — these are set via the `/config` page once you're signed in.

### Production (NixOS)

For NixOS hosts, use the `services.skynetcontrol` module from `module.nix`. It handles state directories, dynamic users, automatic migrations, and systemd hardening:

```nix
services.skynetcontrol = {
  enable = true;
  host = "127.0.0.1";
  port = 8000;
  settings = {
    APP_BASE_URL = "https://net.example.org";
    AUTH_GITHUB_ENABLED = "true";
    AUTH_GITHUB_CLIENT_ID = "Iv1.xxxxxxxx";
  };
};

systemd.services.skynetcontrol.serviceConfig.EnvironmentFile = [
  "/run/secrets/skynetcontrol-env"  # via sops-nix or agenix
];
```

See **[docs/deployment/nix.md](docs/deployment/nix.md)** for the full NixOS setup including reverse proxy + ACME, PostgreSQL, backups, and overlay use.

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
| [docs/deployment/oidc-providers.md](docs/deployment/oidc-providers.md) | Register OAuth/OIDC apps for sign-in |
| [docs/deployment/secrets.md](docs/deployment/secrets.md) | Environment variables and secret management |
| [docs/deployment/app-config-keys.md](docs/deployment/app-config-keys.md) | DB-stored runtime configuration (set via `/config`) |
| [docs/deployment/nix.md](docs/deployment/nix.md) | Production deployment via Nix (NixOS module, OCI image, overlay) |
| [docs/development.md](docs/development.md) | Local development setup |
| [docs/audit-2026-05-27.md](docs/audit-2026-05-27.md) | Most recent security + code audit |
| [docs/superpowers/specs/2026-04-16-winlink-net-manager-design.md](docs/superpowers/specs/2026-04-16-winlink-net-manager-design.md) | Product design spec (architecture, modules, data flow) |

## License

TBD.
