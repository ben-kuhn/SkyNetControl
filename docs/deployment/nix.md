# Nix deployment

SkyNetControl ships three Nix derivations. Pick whichever fits your environment:

| File | Use for |
|------|---------|
| `default.nix` | `nix-build` to produce a runnable Python application. Suitable for ad-hoc deploys, CI smoke tests, or as input to other Nix consumers. |
| `module.nix` | NixOS systemd service — the recommended production path on a NixOS host. Handles state directory, dynamic user, automatic migrations, security hardening. |
| `oci.nix` | OCI container image built with `dockerTools.buildLayeredImage`. For Docker, Podman, Kubernetes — anywhere outside NixOS. |

All three share `default.nix` as the package definition. The frontend is built separately via `frontend.nix` and copied into the package's `share/` tree.

## Building the package

```bash
nix-build default.nix
./result/bin/skynetcontrol-server backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

The build produces two binaries:

- `skynetcontrol-server` — Uvicorn factory entry point.
- `skynetcontrol-alembic` — Alembic CLI wrapped with the bundled migrations and `alembic.ini`. Run it directly to manage the database:
  ```bash
  ./result/bin/skynetcontrol-alembic -c ./result/share/skynetcontrol/alembic.ini upgrade head
  ```

`SKYNET_STATIC_DIR` is baked into the wrapped binaries and points at the built frontend assets, so the SPA is served from the same Uvicorn process — no separate web server needed.

## NixOS module (recommended for production)

### Importing the module

In your NixOS flake or configuration, import `module.nix`:

```nix
{ inputs, ... }:
{
  imports = [
    (import "${inputs.skynetcontrol}/module.nix")
  ];

  services.skynetcontrol = {
    enable = true;
    host = "127.0.0.1";   # bind localhost; front with nginx for TLS
    port = 8000;
    settings = {
      APP_BASE_URL = "https://net.example.org";
      JWT_SECRET_KEY = "$JWT_SECRET";  # see "Secrets" below
      AUTH_GITHUB_ENABLED = "true";
      AUTH_GITHUB_CLIENT_ID = "Iv1.abcdef0123456789";
      AUTH_GITHUB_CLIENT_SECRET = "$GH_CLIENT_SECRET";
    };
  };
}
```

(Without flakes: clone the repo to `/etc/nixos/skynetcontrol`, then `imports = [ /etc/nixos/skynetcontrol/module.nix ];`.)

### What the module does

- Creates a systemd unit `skynetcontrol.service`, started at boot (`wantedBy = [ "multi-user.target" ]`).
- Runs `skynetcontrol-alembic upgrade head` as `ExecStartPre` — migrations apply automatically on every restart.
- Uses `DynamicUser=true` so the service runs as an unprivileged ephemeral user.
- Creates `StateDirectory=skynetcontrol` at `/var/lib/skynetcontrol/` — the SQLite database lives here by default.
- Hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`. `ReadWritePaths` lets it touch the state dir.
- Restarts on failure with a 5s delay.

### Module options

| Option | Default | Notes |
|--------|---------|-------|
| `enable` | `false` | Toggle the service. |
| `port` | `8000` | Listen port. |
| `host` | `127.0.0.1` | Bind address. Use `0.0.0.0` if you're not fronting with a reverse proxy. |
| `stateDir` | `/var/lib/skynetcontrol` | Service state. The default database URL points inside here. |
| `databaseUrl` | `sqlite:////var/lib/skynetcontrol/skynetcontrol.db` | Override to point at PostgreSQL. |
| `settings` | `{}` | Attrset of additional env vars. Any `SKYNET_*` setting from `backend/config.py` can be set here. Keys are uppercased and prefixed with `SKYNET_` automatically (so `JWT_SECRET_KEY` becomes `SKYNET_JWT_SECRET_KEY`). |

### Secrets

`services.skynetcontrol.settings` is plain text — fine for non-secret values like client IDs, but **do not put secrets there**. Wire them in via systemd `EnvironmentFile`:

```nix
# /etc/nixos/configuration.nix
systemd.services.skynetcontrol.serviceConfig.EnvironmentFile = [
  "/run/skynetcontrol/env"
];
```

Then populate `/run/skynetcontrol/env` from your secret store:

```nix
# Using sops-nix
sops.secrets."skynetcontrol-env" = {
  owner = "root";
  group = "root";
  mode = "0400";
  path = "/run/skynetcontrol/env";
};
```

```nix
# Using agenix
age.secrets.skynetcontrol-env = {
  file = ../secrets/skynetcontrol-env.age;
  path = "/run/skynetcontrol/env";
};
```

The env file is just `KEY=value` lines, e.g.:

```
SKYNET_JWT_SECRET_KEY=hex-string-here
SKYNET_AUTH_GITHUB_CLIENT_SECRET=actual-secret
SKYNET_SMTP_PASSWORD=app-password
```

See [secrets.md](secrets.md) for the full list of secret-bearing variables.

### Reverse proxy with TLS

The module binds localhost by default. Front it with nginx (or Caddy, Traefik) for TLS:

```nix
services.nginx = {
  enable = true;
  virtualHosts."net.example.org" = {
    enableACME = true;
    forceSSL = true;
    locations."/" = {
      proxyPass = "http://127.0.0.1:8000";
      proxyWebsockets = true;
    };
  };
};

security.acme.acceptTerms = true;
security.acme.defaults.email = "you@example.org";
```

Remember to set `SKYNET_APP_BASE_URL=https://net.example.org` so OAuth callbacks use the public URL.

### PostgreSQL

If you outgrow SQLite:

```nix
services.postgresql = {
  enable = true;
  ensureDatabases = [ "skynetcontrol" ];
  ensureUsers = [{
    name = "skynetcontrol";
    ensureDBOwnership = true;
  }];
};

services.skynetcontrol = {
  enable = true;
  databaseUrl = "postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql";
};
```

The peer-authenticated socket-path URL avoids putting credentials anywhere. Migrations will run automatically against the new database on next restart.

### Backups

The SQLite default puts state at `/var/lib/skynetcontrol/skynetcontrol.db`. Snapshot that file (with the service stopped, or via `sqlite3 .backup`) to back up everything except secrets:

```bash
sudo systemctl stop skynetcontrol
sudo cp /var/lib/skynetcontrol/skynetcontrol.db /backup/skynetcontrol-$(date +%F).db
sudo systemctl start skynetcontrol
```

For PostgreSQL, use `pg_dump` per your normal database backup workflow.

### Updating

The module re-evaluates whenever the `skynetcontrol` package input changes. Migrations run via `ExecStartPre` on each restart, so a `nixos-rebuild switch` is the full update path:

```bash
sudo nixos-rebuild switch
```

If migrations fail, the unit fails to start and systemd surfaces the error in `journalctl -u skynetcontrol`. Roll back with `nixos-rebuild switch --rollback`.

## OCI image (Docker, Podman, Kubernetes)

The CI pipeline publishes a fresh image to GHCR on every push to `main` and on every version tag:

**`ghcr.io/ben-kuhn/skynetcontrol:latest`** — track main
**`ghcr.io/ben-kuhn/skynetcontrol:0.1.0`** — pinned version (set by `v0.1.0` tags)

You can also build locally:

```bash
nix-build oci.nix
docker load < result    # or: podman load < result
```

This produces `skynetcontrol:latest` locally. The build is fully reproducible — same Nix inputs produce a byte-identical image — no Dockerfile, no `apt-get update` surprises.

### Running

```bash
# Migrations (one-time per upgrade)
docker run --rm \
  -v skynetcontrol-data:/data \
  --env-file /path/to/skynetcontrol.env \
  --entrypoint skynetcontrol-alembic \
  ghcr.io/ben-kuhn/skynetcontrol:latest \
  upgrade head

# Server
docker run -d \
  --name skynetcontrol \
  --restart unless-stopped \
  -p 8000:8000 \
  -v skynetcontrol-data:/data \
  --env-file /path/to/skynetcontrol.env \
  ghcr.io/ben-kuhn/skynetcontrol:latest
```

The image:

- Binds `0.0.0.0:8000`.
- Defaults `SKYNET_DATABASE_URL=sqlite:////data/skynetcontrol.db` — mount a volume at `/data` to persist the database.
- Sets `SKYNET_STATIC_DIR` to the bundled frontend assets.
- Does NOT auto-run migrations. Run `skynetcontrol-alembic upgrade head` before each upgrade. (The NixOS module handles this automatically.)

The bundled `skynetcontrol-alembic` entry point is wrapped to know where the migrations and `alembic.ini` are inside the Nix store — you don't need to pass `-c`.

### Pushing a private build

If you forked the repo and want your own published images, the included `.github/workflows/container.yml` already lowercases the repo name to satisfy GHCR. Just enable Actions on your fork.

To push from a local Nix build manually:

```bash
docker tag skynetcontrol:latest ghcr.io/your-handle/skynetcontrol:0.1.0
docker push ghcr.io/your-handle/skynetcontrol:0.1.0
```

(GHCR rejects uppercase repo names — pass the path in lowercase.)

Or use `skopeo copy` to publish directly from the build output without a local Docker daemon:

```bash
skopeo copy docker-archive:result docker://ghcr.io/your-handle/skynetcontrol:0.1.0
```

### Kubernetes

A minimal Deployment for the OCI image:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: skynetcontrol
spec:
  replicas: 1   # SQLite — keep at 1, scale by switching to PostgreSQL
  selector:
    matchLabels: { app: skynetcontrol }
  template:
    metadata:
      labels: { app: skynetcontrol }
    spec:
      initContainers:
        - name: migrate
          image: ghcr.io/ben-kuhn/skynetcontrol:latest
          command: ["skynetcontrol-alembic", "upgrade", "head"]
          envFrom:
            - secretRef:
                name: skynetcontrol-env
          volumeMounts:
            - { name: data, mountPath: /data }
      containers:
        - name: server
          image: ghcr.io/ben-kuhn/skynetcontrol:latest
          ports: [{ containerPort: 8000 }]
          envFrom:
            - secretRef:
                name: skynetcontrol-env
          volumeMounts:
            - { name: data, mountPath: /data }
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: skynetcontrol-data
```

Pair with a Service and Ingress per your cluster's conventions.

## Overlay use in other Nix projects

`default.nix` is overlay-friendly:

```nix
# In your own flake or overlay
final: prev: {
  skynetcontrol = prev.callPackage ./path/to/skynetcontrol/default.nix {};
}
```

Then `pkgs.skynetcontrol` is available anywhere downstream.

## What this guide doesn't cover

- **CI/CD pipeline** — the repo has no GitHub Actions or similar. Building on push and publishing to a registry is left to you.
- **Multi-instance / HA** — SQLite supports one writer; for HA either go PostgreSQL with the same `databaseUrl` pointing at a shared cluster, or run a single instance behind a failover-capable load balancer.
- **nixpkgs submission** — the package is structured for submission but isn't in nixpkgs yet.
