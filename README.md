# SkyNetControl

A web application for managing a weekly Winlink net — schedule, activity planning, participant reminders, check-in tracking, roster generation, and long-term participation records.

Built for ham radio operators running recurring digital nets, with first-class Winlink (PAT mailbox) integration, groups.io / email / Winlink delivery backends, and Claude-powered activity brainstorming.

## Quick start

### Production (Docker / Podman)

A reproducible OCI image is published on every push to `main`:

**`ghcr.io/ben-kuhn/skynetcontrol:latest`**

```bash
# 1. Create an env file with secrets (see docs/deployment/secrets.md
#    and docs/deployment/oidc-providers.md for the full list).
cat > skynetcontrol.env <<'EOF'
SKYNET_JWT_SECRET_KEY=replace-with-openssl-rand-hex-32
SKYNET_APP_BASE_URL=https://net.example.org
SKYNET_AUTH_GITHUB_ENABLED=true
SKYNET_AUTH_GITHUB_CLIENT_ID=Iv1.xxxxxxxx
SKYNET_AUTH_GITHUB_CLIENT_SECRET=xxxxxxxx
EOF

# 2. Run migrations (re-run on every version bump; safe to repeat).
docker run --rm \
  -v skynetcontrol-data:/data \
  --env-file skynetcontrol.env \
  --entrypoint skynetcontrol-alembic \
  ghcr.io/ben-kuhn/skynetcontrol:latest \
  upgrade head

# (The bundled alembic wrapper knows where its config lives; no -c needed.)

# 3. Start the server.
docker run -d \
  --name skynetcontrol \
  --restart unless-stopped \
  -p 8000:8000 \
  -v skynetcontrol-data:/data \
  --env-file skynetcontrol.env \
  ghcr.io/ben-kuhn/skynetcontrol:latest
```

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
