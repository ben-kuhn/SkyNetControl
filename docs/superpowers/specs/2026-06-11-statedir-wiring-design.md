# NixOS module `stateDir` actually works

## Problem

`module.nix` exposes `services.skynetcontrol.stateDir` and its public
documentation (`docs/deployment/nix.md:166-199`) promises:

> Set this to any path the system can write to … and systemd's `StateDirectory=`
> will create it with the dynamic-user ownership on first start.

That promise is not kept. In the current module:

- `databaseUrl` defaults to a hardcoded `sqlite:////var/lib/skynetcontrol/skynetcontrol.db`
  — it never references `cfg.stateDir`.
- `StateDirectory = "skynetcontrol"` is hardcoded — systemd can only manage
  paths under `/var/lib/`, so `StateDirectory=` cannot point at
  `/storage/skynetcontrol` or `/tank/skynetcontrol`.
- `cfg.stateDir` is referenced only in `ReadWritePaths`. That allows writes
  to the custom path but does nothing to *direct* writes there.

Result with `services.skynetcontrol.stateDir = "/storage/skynetcontrol";`:

- `/storage/skynetcontrol` is empty.
- The database lives at `/var/lib/private/skynetcontrol/skynetcontrol.db`
  (DynamicUser + StateDirectory bind-mount).

This bit a real user (W0NE on 2026-06-11) who discovered the missing DB by
inspection.

## Constraints

- The existing default (`/var/lib/skynetcontrol`, DynamicUser) must keep
  working — that's how most installations run today, and migrations from it
  should be possible but not forced.
- The docs already advertise three patterns: `stateDir` override, bind-mount
  over the default, and external PostgreSQL. We don't want to throw any of
  those away.
- The user has set `systemd.services.skynetcontrol.serviceConfig.SupplementaryGroups`
  in their own config to give the service access to the `pat` group. The fix
  must not break that override.

## Design

### Drop `DynamicUser`; introduce a static `skynetcontrol` system user

Trying to make `DynamicUser=true` work with an arbitrary `stateDir` outside
`/var/lib/` requires either pre-chowning a directory we don't yet have a UID
for, or a `BindPaths` over `/var/lib/private/skynetcontrol` whose source we
also can't pre-chown. Every option ends up trading one form of fragility for
another (root-prefixed `ExecStartPre`, world-writable parents, bind-mount
ordering surprises).

The simplest robust answer is a fixed system user. Every comparable NixOS
service that supports a relocatable data directory (`gitea`, `forgejo`,
`matrix-synapse`, `nextcloud`, `peertube`) does this. The cost is
straightforward: a stable `skynetcontrol:skynetcontrol` UID/GID on the host,
versus the systemd-managed dynamic UID isolation.

For a single-instance ham-radio net manager, "the data is owned by
`skynetcontrol`" is a clearer and more maintainable model than dynamic-UID
isolation. Snapshots, backups, and operational `ls` all become predictable.

### Module changes

`module.nix`:

1. Add static user/group:
   ```nix
   users.users.skynetcontrol = {
     isSystemUser = true;
     group = "skynetcontrol";
     home = cfg.stateDir;
     description = "SkyNetControl service user";
   };
   users.groups.skynetcontrol = {};
   ```

2. Pre-create `cfg.stateDir` with correct ownership via `systemd.tmpfiles`:
   ```nix
   systemd.tmpfiles.rules = [
     "d ${cfg.stateDir} 0750 skynetcontrol skynetcontrol - -"
   ];
   ```

3. Replace `DynamicUser = true; StateDirectory = "skynetcontrol";` with:
   ```nix
   User = "skynetcontrol";
   Group = "skynetcontrol";
   WorkingDirectory = cfg.stateDir;
   ReadWritePaths = [ cfg.stateDir ];
   ```

4. Default `databaseUrl` is computed from `cfg.stateDir`:
   ```nix
   default = "sqlite:///${cfg.stateDir}/skynetcontrol.db";
   ```
   (`sqlite:///` + an absolute path starting with `/` produces SQLAlchemy's
   four-slash absolute form.)

### Hardening preserved

Everything else stays: `NoNewPrivileges`, `ProtectSystem = "strict"`,
`ProtectHome`, `PrivateTmp` — none of these required `DynamicUser`.
`SupplementaryGroups` (a `serviceConfig` knob) continues to work unchanged.

## Migration

For an installation that already runs with the current module and the
default `stateDir`:

```bash
systemctl stop skynetcontrol
nixos-rebuild switch           # creates the skynetcontrol user/group
chown -R skynetcontrol:skynetcontrol /var/lib/skynetcontrol
systemctl start skynetcontrol
```

For an installation that *intended* a custom `stateDir` but is silently
writing to `/var/lib/private/skynetcontrol` (the W0NE case):

```bash
systemctl stop skynetcontrol
# Optionally back up first:
cp -a /var/lib/private/skynetcontrol/skynetcontrol.db /root/skynetcontrol-pre-migrate.db
nixos-rebuild switch           # creates user/group + tmpfiles entry
mv /var/lib/private/skynetcontrol/skynetcontrol.db /storage/skynetcontrol/
chown -R skynetcontrol:skynetcontrol /storage/skynetcontrol
systemctl start skynetcontrol
```

The OCI image is unaffected (it ships its own user via `default.nix` /
`oci.nix` and is not consumed via the module).

## Docs to update

- `docs/deployment/nix.md` — replace the "dynamic-user ownership on first
  start" language with the static-user model; update the backup recipe at
  line 248 that currently mentions `chown`-ing dynamic UIDs; add a migration
  note for upgraders.

## Tests

The module itself has no Python tests, but we can add a NixOS VM test under
`tests/nixos/` that asserts the database file lands under a custom
`stateDir`. Out of scope for this initial fix — manual verification on the
W0NE test server is the acceptance criterion.

## Out of scope

- Making the dynamic-UID variant work with custom `stateDir` (see Design
  rationale).
- Adding `user`/`group` module options. Hardcoding `skynetcontrol` keeps the
  surface small; a user override is one-line `users.users.skynetcontrol.uid`
  if a fixed UID is needed.
