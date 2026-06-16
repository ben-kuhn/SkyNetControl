# Nix deployment

SkyNetControl ships three Nix derivations. Pick whichever fits your environment:

| File | Use for |
|------|---------|
| `default.nix` | `nix-build` to produce a runnable Python application. Suitable for ad-hoc deploys, CI smoke tests, or as input to other Nix consumers. |
| `module.nix` | NixOS systemd service — the recommended production path on a NixOS host. Handles state directory, service user, automatic migrations, security hardening. |
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
{ inputs, config, ... }:
{
  imports = [
    (import "${inputs.skynetcontrol}/module.nix")
  ];

  services.skynetcontrol = {
    enable        = true;
    host          = "127.0.0.1";   # bind localhost; front with nginx for TLS
    port          = 8040;
    stateDir      = "/storage/skynetcontrol";
    appBaseUrl    = "https://skynetcontrol.example.org";
    jwtSecretFile = config.age.secrets.skynetcontrol-jwt.path;
  };
  users.users.skynetcontrol.extraGroups = [ "pat" ];
}
```

(Without flakes: clone the repo to `/etc/nixos/skynetcontrol`, then `imports = [ /etc/nixos/skynetcontrol/module.nix ];`.)

`jwtSecretFile` must point to a file containing the raw JWT signing secret (no trailing newline needed). Generate it once:

```bash
openssl rand -hex 32 | sudo tee /etc/skynetcontrol-jwt > /dev/null
sudo chmod 400 /etc/skynetcontrol-jwt
```

The unit reads it via systemd `LoadCredential`, so it is never visible in the Nix store or in `systemctl show`.

### What the module does

- Creates a systemd unit `skynetcontrol.service`, started at boot (`wantedBy = [ "multi-user.target" ]`).
- Runs `skynetcontrol-alembic upgrade head` as `ExecStartPre` — migrations apply automatically on every restart.
- Runs as a static system user `skynetcontrol:skynetcontrol` (created by the module).
- Pre-creates `stateDir` (default `/var/lib/skynetcontrol/`) via `systemd.tmpfiles` with `skynetcontrol:skynetcontrol` ownership — the SQLite database lives here by default.
- Hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`. `ReadWritePaths` lets it touch the state dir.
- Restarts on failure with a 5s delay.

### Module options

| Option | Default | Notes |
|--------|---------|-------|
| `enable` | `false` | Toggle the service. |
| `port` | `8000` | Listen port. |
| `host` | `127.0.0.1` | Bind address. Use `0.0.0.0` if you're not fronting with a reverse proxy. |
| `stateDir` | `/var/lib/skynetcontrol` | Service state. The default database URL points inside here. |
| `databaseUrl` | `sqlite:///${stateDir}/skynetcontrol.db` | Defaults inside `stateDir`. Override to point at PostgreSQL. |
| `appBaseUrl` | *(required)* | Externally-visible URL the user's browser hits (including scheme and any non-default port). Used for OAuth redirect URIs and email links. |
| `jwtSecretFile` | *(required)* | Path to a file containing the JWT signing secret. Read via systemd `LoadCredential` — never lands in the Nix store. |

### After upgrading from a pre-Phase-5 release

If you previously set `services.skynetcontrol.settings.AUTH_*`, `settings.SMTP__*`, or
`settings.PAT_MAILBOX_PATH`, those values were imported into the AppConfig table by
the one-time `import_env_to_app_config` Alembic migration shipped in the earlier release.
You can now manage them via the `/config` admin page (after signing in) or via the
first-boot wizard at `/setup`. Removing the `settings.*` lines from your NixOS config
is the only required step; the values keep working from the database.

You **will** need to add the two new required options (`appBaseUrl` and `jwtSecretFile`)
before the next `nixos-rebuild switch`.

### Secrets

The only secret that belongs in `module.nix` is `jwtSecretFile`. Point it at a file managed by your secret store of choice:

```nix
# Using agenix
age.secrets.skynetcontrol-jwt = {
  file = ../secrets/skynetcontrol-jwt.age;
};
services.skynetcontrol.jwtSecretFile = config.age.secrets.skynetcontrol-jwt.path;
```

```nix
# Using sops-nix
sops.secrets."skynetcontrol-jwt" = {
  owner = "root";
  mode = "0400";
};
services.skynetcontrol.jwtSecretFile = config.sops.secrets."skynetcontrol-jwt".path;
```

Or, for a simple self-managed installation, generate a file outside the Nix store and make it root-readable:

```bash
openssl rand -hex 32 | sudo tee /etc/skynetcontrol-jwt > /dev/null
sudo chmod 400 /etc/skynetcontrol-jwt
```

```nix
services.skynetcontrol.jwtSecretFile = "/etc/skynetcontrol-jwt";
```

All other previously-secret values (OAuth client secrets, SMTP passwords, etc.) are stored in the AppConfig table and managed via the `/config` admin page — no env vars needed.

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

Remember to set `appBaseUrl = "https://net.example.org"` in `services.skynetcontrol` so OAuth callbacks use the public URL.

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

### Custom storage location

The NixOS module exposes two storage knobs:

- `services.skynetcontrol.stateDir` — where service state lives. Defaults to `/var/lib/skynetcontrol`. Set this to any path the system can write to:

  ```nix
  services.skynetcontrol.stateDir = "/tank/skynetcontrol";
  ```

  The module pre-creates this directory via `systemd.tmpfiles` with `skynetcontrol:skynetcontrol` ownership (mode `0750`) and adds it to `ReadWritePaths`. The parent directory must exist (e.g. the ZFS dataset must be mounted) before the tmpfiles rule runs.

- `services.skynetcontrol.databaseUrl` — the SQLAlchemy URL. Defaults to `sqlite:////var/lib/skynetcontrol/skynetcontrol.db` (note the four slashes — SQLAlchemy's absolute-path SQLite form). Override this to put the DB file outside `stateDir` or to use a different engine:

  ```nix
  services.skynetcontrol.databaseUrl = "sqlite:////tank/skynetcontrol/skynetcontrol.db";
  ```

#### Pattern 1: Put state on a ZFS dataset

Create a dedicated dataset so you can snapshot just the SkyNetControl state:

```bash
sudo zfs create -o mountpoint=/tank/skynetcontrol tank/skynetcontrol
```

Then point the module at it:

```nix
services.skynetcontrol.stateDir = "/tank/skynetcontrol";
services.skynetcontrol.databaseUrl = "sqlite:////tank/skynetcontrol/skynetcontrol.db";
```

After `nixos-rebuild switch`, `systemd-tmpfiles` will create the directory with `skynetcontrol:skynetcontrol` ownership on next service start. The DB lives entirely on the dedicated dataset — snapshots, replication, and quotas all apply.

#### Pattern 2: Bind-mount over the default location

If you don't want to change `stateDir` (e.g. another tool already expects `/var/lib/skynetcontrol`), bind-mount the real storage in:

```nix
fileSystems."/var/lib/skynetcontrol" = {
  device = "/tank/skynetcontrol";
  options = [ "bind" ];
};
```

This keeps the module's defaults and routes all state I/O through the new device. The bind mount needs to be in place before `skynetcontrol.service` starts — NixOS orders fileSystems before multi-user.target by default, so this is automatic.

#### Pattern 3: External PostgreSQL

For multi-instance setups or just to avoid SQLite altogether, swap the URL:

```nix
services.skynetcontrol.databaseUrl =
  "postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql";
```

Run a local `services.postgresql.enable = true;` with a matching role and DB, or point at a remote cluster — peer auth via the socket path is the cleanest for a co-located DB. Migrations run automatically on next service restart.

### Backups

Pick whichever fits your storage layout. All three patterns coexist.

#### Filesystem snapshots (when state is on ZFS/btrfs)

If you put state on its own dataset/subvolume, snapshots are the simplest backup:

```bash
sudo zfs snapshot tank/skynetcontrol@$(date +%F)
```

For automatic daily snapshots use `services.zfs.autoSnapshot.enable = true;` (or `sanoid`, `znapzend`, etc.). Restore is just `zfs clone` or `zfs rollback` — the service does not need to be stopped to take a snapshot, since ZFS gives you an atomic point-in-time view even while SQLite is mid-write.

#### Online SQLite snapshot via `sqlite3 .backup`

If you're not on a snapshotting filesystem, use SQLite's built-in online backup. It produces a consistent copy without stopping the service:

```bash
sudo sqlite3 /var/lib/skynetcontrol/skynetcontrol.db \
  ".backup '/backup/skynetcontrol-$(date +%F).db'"
```

(Run the backup as root; the resulting file will be owned by root and readable by whatever restic / borg / rsync job picks it up next.)

A systemd timer makes this nightly:

```nix
systemd.services."skynetcontrol-backup" = {
  description = "Online backup of SkyNetControl SQLite DB";
  serviceConfig.Type = "oneshot";
  script = ''
    ${pkgs.sqlite}/bin/sqlite3 \
      ${config.services.skynetcontrol.stateDir}/skynetcontrol.db \
      ".backup '/backup/skynetcontrol-$(date +%F).db'"
  '';
};
systemd.timers."skynetcontrol-backup" = {
  wantedBy = [ "timers.target" ];
  timerConfig = { OnCalendar = "daily"; Persistent = true; };
};
```

#### Stop-and-copy (lowest-tech fallback)

When you just want a one-shot before an upgrade:

```bash
sudo systemctl stop skynetcontrol
sudo cp /var/lib/skynetcontrol/skynetcontrol.db /backup/skynetcontrol-$(date +%F).db
sudo systemctl start skynetcontrol
```

#### restic / borg over the state dir

The whole `stateDir` is the unit of backup. Any backup tool that walks a directory works — point it at `services.skynetcontrol.stateDir`. With SQLite's WAL mode this is *mostly* safe, but for a guaranteed-consistent backup pair it with stopping the service or with `sqlite3 .backup` into a snapshot path that restic then captures.

#### PostgreSQL

When `databaseUrl` points at PostgreSQL, ignore the SQLite recipes and use `pg_dump` / `pg_basebackup` per your normal database backup workflow. The `stateDir` then contains only ephemeral runtime state.

### Moving between database backends

The Nix package ships `skynetcontrol-db-copy`, which uses SQLAlchemy reflection to copy every row from one DB URL to another. Use it to:

- Migrate from SQLite to PostgreSQL (or back) without writing custom dumps.
- Move from one host's DB to another's by copying over the network: the source URL can be a remote PostgreSQL URL.
- Promote a backup snapshot back into a live engine (read from the snapshot DB, write into the running one — when the live one is empty).

Recipe (SQLite → PostgreSQL):

```bash
# 1. Stop the service so the source DB isn't being written to.
sudo systemctl stop skynetcontrol

# 2. Make sure the new target is migrated to the same head as the source.
sudo SKYNET_DATABASE_URL='postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql' \
  skynetcontrol-alembic upgrade head

# 3. Copy the rows. --replace truncates the freshly-migrated target first
#    (it has seed rows from migrations — default templates, etc.).
sudo skynetcontrol-db-copy --replace \
  sqlite:////var/lib/skynetcontrol/skynetcontrol.db \
  'postgresql+psycopg://skynetcontrol@/skynetcontrol?host=/run/postgresql'

# 4. Flip the module to the new URL and restart.
# (edit services.skynetcontrol.databaseUrl in configuration.nix)
sudo nixos-rebuild switch
```

By default the command refuses to copy into a target that's unmigrated or already has data — explicit safety to prevent clobbering. `--replace` wipes the target before copying; omit it only when you're certain the target is truly empty (which a freshly-migrated DB is *not*, since seed migrations insert default templates). Reverse the source / target arguments to roll back.

### PAT integration (hourly Winlink fetch)

SkyNetControl reads check-ins out of a PAT mailbox directory but does not fetch mail from Winlink itself. Pair the service with a systemd timer that runs `pat connect telnet` once an hour. The two services need to agree on a mailbox directory; the cleanest approach is to give PAT its own static user and grant the `skynetcontrol` user read access via a shared group.

```nix
{ config, lib, pkgs, ... }:

{
  environment.systemPackages = [ pkgs.pat ];

  # Static user for PAT.
  users.groups.pat = {};
  users.users.pat = {
    isSystemUser = true;
    group = "pat";
    home = "/var/lib/pat";
    createHome = true;
  };

  # The mailbox directory needs to be group-readable so SkyNetControl
  # (a different UID, joined to the pat group via SupplementaryGroups
  # below) can walk it.
  systemd.tmpfiles.rules = [
    "d /var/lib/pat                              0750 pat pat - -"
    "d /var/lib/pat/.config                      0750 pat pat - -"
    "d /var/lib/pat/.config/pat                  0750 pat pat - -"
    "d /var/lib/pat/.local                       0750 pat pat - -"
    "d /var/lib/pat/.local/share                 0750 pat pat - -"
    "d /var/lib/pat/.local/share/pat             0750 pat pat - -"
    "d /var/lib/pat/.local/share/pat/mailbox     0750 pat pat - -"
  ];

  # Hourly fetch. PAT reads ~/.config/pat/config.json — log in once as
  # the pat user (`sudo -u pat pat configure`) before enabling the
  # timer, so it has your callsign + Winlink password.
  systemd.services."pat-fetch" = {
    description = "Pull Winlink mail via PAT (telnet/CMS)";
    serviceConfig = {
      Type = "oneshot";
      User = "pat";
      Group = "pat";
      ExecStart = "${pkgs.pat}/bin/pat connect telnet";
      TimeoutStartSec = "5m";
      # PAT prints progress to stderr — surface it in the journal.
      StandardOutput = "journal";
      StandardError = "journal";
    };
  };

  systemd.timers."pat-fetch" = {
    description = "Hourly Winlink fetch";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "hourly";
      Persistent = true;        # run a missed tick after a reboot
      RandomizedDelaySec = "5m"; # don't stampede every PAT user on the hour
    };
  };

  # Let the skynetcontrol user read the pat-owned mailbox.
  users.users.skynetcontrol.extraGroups = [ "pat" ];
}
```

First-time setup, once after `nixos-rebuild switch`:

```bash
sudo -u pat pat configure   # writes ~pat/.config/pat/config.json
sudo systemctl start pat-fetch.service   # one manual fetch to seed the mailbox
sudo systemctl list-timers pat-fetch     # confirm scheduling
journalctl -u pat-fetch -f               # follow live
```

Then in SkyNetControl's `/config` page, enable **Auto-Scanner** and set **Scan Interval** to something less than 60 minutes — typically 5 — so check-ins surface promptly after each PAT fetch.

Two notes:

- **Transport.** `telnet` reaches Winlink's CMS over the internet — no radio needed. Swap for `ax25`, `ardop`, or `vara` if you're pulling over RF, and accept that those transports usually want a tighter loop (or a long-running `pat http` service) instead of an hourly oneshot.
- **Mailbox path matches the user.** PAT writes to `~/.local/share/pat/mailbox/<CALLSIGN>` — the `~` resolves to `/var/lib/pat` because that's what `users.users.pat.home` is set to. If you move that home, update the **PAT Mailbox Path** in the `/config` admin page and the tmpfiles rules together.

### Updating

The module re-evaluates whenever the `skynetcontrol` package input changes. Migrations run via `ExecStartPre` on each restart, so a `nixos-rebuild switch` is the full update path:

```bash
sudo nixos-rebuild switch
```

If migrations fail, the unit fails to start and systemd surfaces the error in `journalctl -u skynetcontrol`. Roll back with `nixos-rebuild switch --rollback`.

### Migrating from older versions (`DynamicUser`)

Earlier revisions of the module ran the service under systemd `DynamicUser=true`, which kept the database under `/var/lib/private/skynetcontrol/`. The current module uses a static `skynetcontrol:skynetcontrol` system user, and `stateDir` is now actually wired into the database URL.

After upgrading, before starting the new unit:

```bash
sudo systemctl stop skynetcontrol
sudo nixos-rebuild switch        # creates the skynetcontrol user/group and stateDir
# Default-stateDir installs:
sudo chown -R skynetcontrol:skynetcontrol /var/lib/skynetcontrol
# Custom-stateDir installs (where the old DB silently went to /var/lib/private/<svc>):
sudo mv /var/lib/private/skynetcontrol/skynetcontrol.db /your/statedir/
sudo chown -R skynetcontrol:skynetcontrol /your/statedir
sudo systemctl start skynetcontrol
```

Any custom `serviceConfig.SupplementaryGroups` override (e.g. for PAT mailbox access) should be replaced with `users.users.skynetcontrol.extraGroups = [ … ];` — the static user makes the secondary-group mechanism the natural one.

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

### PAT integration (hourly Winlink fetch)

The SkyNetControl image only *reads* a PAT mailbox; it doesn't talk to Winlink. You run PAT on the host (or in its own container) on an hourly cron / systemd timer, and share the mailbox directory with the SkyNetControl container via a bind mount.

This guide assumes you can edit a few files, install a package, and run `crontab -e` — no Nix experience needed.

**1. Install and configure PAT on the host.**

Most distros have it:

```bash
# Debian/Ubuntu
sudo apt install pat
# Fedora/RHEL
sudo dnf install pat
# Arch
sudo pacman -S pat
```

(Or grab a binary from <https://getpat.io/> if your distro doesn't package it.)

Pick the host user PAT will run as. Easiest: just use the user you log in as. Throughout the examples below this is **`ham`** — substitute your own.

```bash
pat configure   # opens an editor; set your callsign + Winlink password
pat connect telnet   # one-shot fetch; confirms the config works
```

After a successful fetch PAT has created `~/.local/share/pat/mailbox/<YOURCALL>/` with `in/`, `out/`, `sent/`, `archive/`. That `in/` directory is what SkyNetControl will scan.

**2. Make the mailbox directory available to the container.**

The SkyNetControl OCI image runs as **root** inside the container by default, so it can read any host directory you bind-mount in. No `chown` gymnastics needed — the only requirement is that the host path exists before you start the container.

Start (or restart) the container with two added flags: the bind mount and a `SKYNET_PAT_MAILBOX_PATH` env var pointing at it.

```bash
docker run -d \
  --name skynetcontrol \
  --restart unless-stopped \
  -p 8000:8000 \
  -v skynetcontrol-data:/data \
  -v /home/ham/.local/share/pat/mailbox:/pat-mailbox:ro \
  -e SKYNET_PAT_MAILBOX_PATH=/pat-mailbox/W0NE \
  --env-file /path/to/skynetcontrol.env \
  ghcr.io/ben-kuhn/skynetcontrol:latest
```

What's going on:

- `-v /home/ham/.local/share/pat/mailbox:/pat-mailbox:ro` — bind-mount PAT's entire mailbox directory tree into the container, read-only. Read-only is correct: SkyNetControl never writes here; it only parses what PAT delivers.
- `-e SKYNET_PAT_MAILBOX_PATH=/pat-mailbox/W0NE` — point the in-app config at the per-callsign subdirectory. Replace `W0NE` with your callsign.

**Don't want to run the container as root?** Add `--user $(id -u ham):$(id -g ham)` to the `docker run` command. Then the bind mount can stay as-is — PAT's mailbox is owned by `ham`, the container now runs as `ham`, the UIDs match.

If your `skynetcontrol-data` named volume already exists and was created under root, switching to `--user` will require fixing its ownership: `docker run --rm -v skynetcontrol-data:/data --user 0 alpine chown -R <uid>:<gid> /data`.

**3. Schedule the hourly fetch.**

The simplest cron entry, in `crontab -e` as your host user:

```cron
# Fetch Winlink mail every hour at :07 (the offset avoids the top-of-the-hour stampede)
7 * * * * /usr/bin/pat connect telnet >> ~/.local/state/pat-fetch.log 2>&1
```

(Adjust the binary path if `which pat` reports a different location.)

Confirm with `tail -f ~/.local/state/pat-fetch.log` after the next tick.

If you're on a systemd-based host and prefer a timer (more visibility via `systemctl status`), drop these two files in `~/.config/systemd/user/` and run `systemctl --user enable --now pat-fetch.timer`:

```ini
# ~/.config/systemd/user/pat-fetch.service
[Unit]
Description=Pull Winlink mail via PAT (telnet/CMS)

[Service]
Type=oneshot
ExecStart=/usr/bin/pat connect telnet
TimeoutStartSec=5m
```

```ini
# ~/.config/systemd/user/pat-fetch.timer
[Unit]
Description=Hourly Winlink fetch

[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=5m

[Install]
WantedBy=timers.target
```

User-level systemd timers need `loginctl enable-linger ham` so they keep running when you're not logged in.

**4. Enable the in-app scanner.**

In SkyNetControl's `/config` page, set **Auto-Scanner** to on and **Scan Interval** to 5 minutes. Now the flow is: every hour PAT pulls Winlink mail into `~/.local/share/pat/mailbox/<CALL>/in/`, and within 5 minutes SkyNetControl notices the new files and parses out check-ins.

**Troubleshooting permissions.** If the `/checkins` page never shows new check-ins after a real fetch:

- `ls -l ~/.local/share/pat/mailbox/<CALL>/in/` — confirm PAT wrote files there as the host user you expect.
- `docker exec skynetcontrol ls /pat-mailbox/<CALL>/in/` — confirm the container sees the same files (and isn't getting an empty directory, which would mean the bind mount is wrong).
- `docker exec skynetcontrol cat /proc/self/status | head -1` — confirm what user the container's main process is running as.

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
