# Secrets Management

SkyNetControl uses environment variables for all secrets. Pydantic Settings loads them with the `SKYNET_` prefix.

## Required Secrets

| Variable | Purpose | How to Generate |
|----------|---------|----------------|
| `SKYNET_JWT_SECRET_KEY` | JWT signing key | `openssl rand -hex 32` |
| `SKYNET_DATABASE_URL` | Database connection string | Provider-specific |

## Auth Provider Credentials

Each enabled provider needs a client ID and secret from the provider's developer console:

| Provider | Client ID | Client Secret | Extra |
|----------|-----------|--------------|-------|
| Google | `SKYNET_AUTH_GOOGLE_CLIENT_ID` | `SKYNET_AUTH_GOOGLE_CLIENT_SECRET` | [Google Cloud Console](https://console.cloud.google.com/apis/credentials) |
| Microsoft | `SKYNET_AUTH_MICROSOFT_CLIENT_ID` | `SKYNET_AUTH_MICROSOFT_CLIENT_SECRET` | [Azure Portal](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps) |
| GitHub | `SKYNET_AUTH_GITHUB_CLIENT_ID` | `SKYNET_AUTH_GITHUB_CLIENT_SECRET` | [GitHub Developer Settings](https://github.com/settings/developers) |
| Discord | `SKYNET_AUTH_DISCORD_CLIENT_ID` | `SKYNET_AUTH_DISCORD_CLIENT_SECRET` | [Discord Developer Portal](https://discord.com/developers/applications) |
| Facebook | `SKYNET_AUTH_FACEBOOK_CLIENT_ID` | `SKYNET_AUTH_FACEBOOK_CLIENT_SECRET` | [Meta Developer Dashboard](https://developers.facebook.com/apps/) |
| Generic OIDC (multi) | `SKYNET_AUTH_OIDC_<SLUG>_CLIENT_ID` | `SKYNET_AUTH_OIDC_<SLUG>_CLIENT_SECRET` | Per provider, also set `_ENABLED`, `_ISSUER_URL`, `_NAME`. `<SLUG>` is uppercase + underscores in the env var; the URL slug uses lowercase + dashes (e.g. `MY_IDP` ↔ `my-idp`). Repeat for as many OIDC providers as you need. |

Enable a provider by setting `SKYNET_AUTH_{PROVIDER}_ENABLED=true`.

## SMTP Credentials (Optional)

| Variable | Purpose | Default |
|----------|---------|---------|
| `SKYNET_SMTP_HOST` | SMTP server | (empty — email disabled) |
| `SKYNET_SMTP_PORT` | SMTP port | `587` |
| `SKYNET_SMTP_USERNAME` | SMTP login | (empty) |
| `SKYNET_SMTP_PASSWORD` | SMTP password | (empty) |
| `SKYNET_SMTP_USE_TLS` | Use STARTTLS | `true` |
| `SKYNET_SMTP_FROM_ADDRESS` | From header | (empty) |

If `SKYNET_SMTP_HOST` is not set, email notifications are silently disabled.

## Deployment Patterns

### NixOS with sops-nix

```nix
sops.secrets."skynetcontrol/env" = {};

systemd.services.skynetcontrol.serviceConfig.EnvironmentFile =
  config.sops.secrets."skynetcontrol/env".path;
```

### NixOS with agenix

```nix
age.secrets.skynetcontrol-env.file = ../secrets/skynetcontrol-env.age;

systemd.services.skynetcontrol.serviceConfig.EnvironmentFile =
  config.age.secrets.skynetcontrol-env.path;
```

### systemd EnvironmentFile

```ini
# /etc/skynetcontrol/env (mode 0600, owned by service user)
SKYNET_JWT_SECRET_KEY=hex-string-here
SKYNET_AUTH_GOOGLE_ENABLED=true
SKYNET_AUTH_GOOGLE_CLIENT_ID=your-client-id
SKYNET_AUTH_GOOGLE_CLIENT_SECRET=your-client-secret
SKYNET_SMTP_HOST=smtp.example.com
SKYNET_SMTP_PASSWORD=app-password-here
```

### Docker / OCI

```bash
docker run --env-file /path/to/env ghcr.io/ben-kuhn/skynetcontrol:latest
```

## Do NOT

- Commit secrets to git
- Use the default `jwt_secret_key` value (`change-me-in-production`) in production
- Pass secrets as command-line arguments (visible in `ps`)
