# Secrets Management

SkyNetControl loads three bootstrap secrets from the environment at startup. Everything else — OAuth provider credentials, SMTP password, integration API keys — lives in the `app_config` table, encrypted at rest, configured through the first-boot wizard at `/setup` or the admin config page once setup is complete.

For day-to-day procedures (key rotation, recovery tokens, registration toggle, behind-proxy configuration), see `operations.md`. This file documents only the env-var surface and the storage model.

## Bootstrap env vars

| Variable | Purpose | How to generate |
| --- | --- | --- |
| `SKYNET_JWT_SECRET_KEY` | JWT signing key. App refuses to start on the placeholder default. | `openssl rand -hex 32` |
| `SKYNET_DATABASE_URL` | SQLAlchemy URL (SQLite or Postgres). | Provider-specific |
| `SKYNET_APP_BASE_URL` | Externally-visible base URL. | Match what the browser sees |
| `SKYNET_SECRETS_KEY` *(optional)* | Separate AES key for at-rest credential encryption. Falls back to `SKYNET_JWT_SECRET_KEY` when unset. | `openssl rand -hex 32` |

`SKYNET_JWT_SECRET_KEY` doubles as the at-rest encryption key when `SKYNET_SECRETS_KEY` is empty. For installs that want independent rotation cadences, set both — rotating one then won't invalidate the other.

## At-rest encryption

OAuth `client_secret` and SMTP `password` rows in `app_config` are stored as AES-256-GCM envelopes (`enc:v1:<base64(nonce || ciphertext+tag)>`). The key is HKDF-SHA256 derived from `SKYNET_SECRETS_KEY` (or its fallback) with a fixed salt and domain-separation info string.

Plaintext rows from before the encryption rolled out pass through `decrypt()` unchanged and are re-encrypted on the next admin save. To migrate existing plaintext rows in one pass:

```bash
skynetcontrol-recovery rotate-secrets
```

To rotate the key itself, see the [Key rotation](operations.md#key-rotation) section of the operations runbook.

## Provider / SMTP credentials

These are **no longer set via env vars**. They live in the database, configured through:

- the first-boot wizard at `/setup` (initial setup),
- the recovery wizard at `/recovery` (after minting a recovery token),
- the admin config page (post-setup, day-to-day changes).

The typed routes mask secret values as `"***"` on GET. The bulk `GET /api/config/` endpoint applies the same masking via a substring allowlist (`api_key`, `password`, `secret`, `token`).

## Deployment patterns

The patterns below all expose the three bootstrap env vars to the process. None of them touch provider or SMTP credentials — those go through the wizard.

### NixOS module

```nix
services.skynetcontrol = {
  enable = true;
  appBaseUrl = "https://net.example.org";
  jwtSecretFile = "/etc/skynetcontrol-jwt";     # 0600, root-owned
  secretsKeyFile = "/etc/skynetcontrol-secrets"; # optional, same shape
};
```

Both files are loaded via systemd `LoadCredential`, so they never appear in the Nix store.

### NixOS with sops-nix

```nix
sops.secrets."skynetcontrol/jwt" = {};
sops.secrets."skynetcontrol/secrets" = {};

services.skynetcontrol = {
  enable = true;
  appBaseUrl = "https://net.example.org";
  jwtSecretFile = config.sops.secrets."skynetcontrol/jwt".path;
  secretsKeyFile = config.sops.secrets."skynetcontrol/secrets".path;
};
```

### NixOS with agenix

```nix
age.secrets.skynet-jwt.file = ../secrets/skynet-jwt.age;
age.secrets.skynet-secrets.file = ../secrets/skynet-secrets.age;

services.skynetcontrol = {
  enable = true;
  appBaseUrl = "https://net.example.org";
  jwtSecretFile = config.age.secrets.skynet-jwt.path;
  secretsKeyFile = config.age.secrets.skynet-secrets.path;
};
```

### systemd EnvironmentFile

```ini
# /etc/skynetcontrol/env  (mode 0600, owned by service user)
SKYNET_DATABASE_URL=sqlite:///var/lib/skynetcontrol/skynetcontrol.db
SKYNET_JWT_SECRET_KEY=<hex-string>
SKYNET_APP_BASE_URL=https://net.example.org
# Optional:
# SKYNET_SECRETS_KEY=<separate-hex-string>
# SKYNET_TRUSTED_PROXIES=127.0.0.1
```

### Docker / OCI

```bash
docker run --env-file /path/to/env ghcr.io/ben-kuhn/skynetcontrol:latest
```

## Do NOT

- Commit any of the bootstrap secrets to git.
- Use the placeholder `SKYNET_JWT_SECRET_KEY=change-me-in-production` in production — the app refuses to start.
- Pass secrets as command-line arguments (visible in `ps`).
- Roll `SKYNET_SECRETS_KEY` without first capturing the old value — encrypted credentials become unrecoverable until you run `rotate-secrets --from-key OLD` to migrate them.
