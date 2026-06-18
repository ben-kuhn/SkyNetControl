# Operations Runbook

Day-to-day operating procedures for a running SkyNetControl deployment. For first-time installation, start with the README. For initial OIDC provider setup, see `oidc-providers.md`. Bootstrap env vars are documented at the top here.

## Bootstrap configuration

Only three env vars are required at startup. Everything else lives in the database and is configured through the first-boot wizard or the admin config page.

| Variable | Required | Purpose |
| --- | --- | --- |
| `SKYNET_DATABASE_URL` | yes | SQLAlchemy URL. SQLite (`sqlite:///path/to/skynetcontrol.db`) or PostgreSQL (`postgresql+psycopg2://...`). |
| `SKYNET_JWT_SECRET_KEY` | yes | JWT signing key. Generate with `openssl rand -hex 32`. The app refuses to start on the placeholder default. |
| `SKYNET_APP_BASE_URL` | yes | Externally-visible base URL of the deployment. Used to build OAuth redirect URIs and links in transactional email. Must match what the browser sees (scheme + host + port). |

Optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SKYNET_SECRETS_KEY` | empty | Separate AES key material for at-rest credential encryption. When unset, the JWT secret is reused. Setting this lets the two keys rotate independently. |
| `SKYNET_TRUSTED_PROXIES` | empty | Comma-separated peer-IP allowlist. When the connecting peer is in the list, the rate limiter trusts `CF-Connecting-IP` / `X-Real-IP` / `X-Forwarded-For` for the real client IP. Required behind Cloudflare or any other reverse proxy or you'll bucket all traffic under a single IP. |
| `SKYNET_STATIC_DIR` | `frontend/dist` | Where the SPA bundle lives. The Nix install wraps this to point at the package output. |
| `SKYNET_DEBUG` | `false` | Verbose logging. Don't enable in production. |

## First-boot setup

After the first start with an empty database, hit `/setup` in a browser. The wizard walks four steps:

1. **Net basics**: net-control callsign, Winlink address, app base URL.
2. **OAuth provider**: pick a fixed provider (Google / Microsoft / GitHub / Discord / Facebook) or a custom OIDC issuer. The page shows the single redirect URI you need to register at the IdP — that same URI is used both by the wizard itself (Step 4) and by everyday sign-in after.
3. **SMTP** (skippable): SMTP server credentials for transactional email.
4. **Claim admin**: redirects you to the IdP. Successful sign-in becomes the first admin user.

`setup_completed` is sentinel-flagged in `app_config` once the wizard finishes; subsequent hits on `/setup` are 410'd until the sentinel is unset (which happens only via the recovery flow below).

## Recovery (locked out of the admin account)

If you get locked out — broken OIDC config, wrong callsign claimed, IdP unavailable — mint a recovery token from the host's shell:

```bash
skynetcontrol-recovery mint-admin-token --ttl 10m
```

The CLI prints a single-use plaintext token plus a claim URL. Open the URL in a browser within the TTL window. The browser session enters "recovery mode" — the setup wizard reappears, pre-filled with the existing configuration, and the admin can edit OAuth credentials / SMTP / net basics in place. Each step saves immediately.

Other recovery subcommands:

```bash
skynetcontrol-recovery list-tokens          # outstanding (unused, unexpired)
skynetcontrol-recovery revoke <hex-prefix>  # mark all matching tokens as used
```

Recovery tokens are stored hashed (SHA-256 of the plaintext). The plaintext is shown once at mint time and never persisted.

## Registration toggle

`registration_open` in app_config (also surfaced as a switch on the admin config page) gates new sign-ups. Default is `true` — anyone who can sign in through the IdP becomes a `PENDING` user. Toggle to `false` to refuse new subjects; existing users continue to sign in normally. Useful for nets that batch-onboard known operators or have already enrolled everyone.

## Key rotation

The two bootstrap keys have different rotation semantics.

### Rotating `SKYNET_JWT_SECRET_KEY`

Invalidates every issued JWT immediately — all users have to sign back in. Also affects:

- Encrypted OAuth / SMTP credentials in `app_config`, **if `SKYNET_SECRETS_KEY` is not set**. In that case the JWT secret is doing double duty as the at-rest encryption key, and rotating it stops decrypt from working until you re-encrypt. See the key-migration procedure below.
- Recovery cookies (issued by `recovery_claim`) become invalid immediately.

To avoid the coupling, set `SKYNET_SECRETS_KEY` to a separate value and rotate the two independently.

### Rotating `SKYNET_SECRETS_KEY`

```bash
# 1. Mint a new key.
NEW_KEY=$(openssl rand -hex 32)

# 2. Update SKYNET_SECRETS_KEY in your env-var source (systemd LoadCredential
#    file, sops secret, EnvironmentFile, etc.) to the new value.

# 3. Restart the service so the new key is loaded.

# 4. Migrate existing rows. Run the CLI with the OLD key value:
skynetcontrol-recovery rotate-secrets --from-key "$OLD_KEY"
```

The CLI walks every sensitive `app_config` row (anything matching `api_key`/`password`/`secret`/`token`), tries to decrypt under the current key, and on failure tries the supplied `--from-key`. Rows that decrypt under either are re-encrypted under the current key. Anything that decrypts under neither is flagged to stderr; the operator has to re-enter that credential through the admin config page.

If you forget `--from-key` after a rotation, `rotate-secrets` prints a warning for every unrecoverable row but doesn't corrupt them. Re-run with the right `--from-key` to recover.

## Behind a reverse proxy (Cloudflare, nginx, Caddy, etc.)

If anything sits between the public internet and uvicorn, the rate limiter will bucket every visitor under the proxy's IP unless you tell it which proxies to trust:

```ini
# systemd EnvironmentFile / docker --env / sops secret
SKYNET_TRUSTED_PROXIES=127.0.0.1,::1
```

Set this to whichever IP(s) connect from the proxy to uvicorn (loopback for a same-host proxy, the proxy's private IP for separate hosts). Once trusted, the limiter consults `CF-Connecting-IP` (Cloudflare's canonical), `X-Real-IP` (nginx convention), then the right-most non-trusted entry of `X-Forwarded-For` in that order.

`TrustedHostMiddleware` is attached automatically when `SKYNET_APP_BASE_URL` is non-localhost. It accepts the configured hostname plus `localhost` / `127.0.0.1` (so internal health checks work). If your proxy passes a different `Host` header, set `SKYNET_APP_BASE_URL` to match.

### Cloudflare-specific notes

- Use a cloudflared tunnel or set `SKYNET_TRUSTED_PROXIES=127.0.0.1` if the tunnel terminates on the same host as uvicorn. Without this, your own admin account shares a rate-limit bucket with every other Cloudflare-fronted user.
- Cloudflare strips and re-issues HSTS / CSP headers depending on rules. The CSP emitted by SkyNetControl is appropriate for an origin server; verify it round-trips through CF if you're seeing browser-side blocks.
- Cache-Control on `index.html` is `no-cache`; assets under `/assets/*` are `immutable` with a one-year max-age. Cloudflare's caching defaults respect both correctly.

## CI

After any push to `main`, wait for the `CI` and `Container` workflows to go green before doing anything else:

```bash
gh run list --branch main --limit 4 --json status,conclusion,name,headSha
```

`CI` runs `ruff check backend/ tests/` plus the pytest suite. `Container` builds the OCI image via Nix — failures there usually mean `frontend.nix`'s `npmDepsHash` needs a refresh after a `package-lock.json` change. The CI failure-mode is documented in `CLAUDE.md`.

## Auditing

The `audit_logs` table records every privileged mutation (config writes, OAuth-provider CRUD, SMTP CRUD, role changes, callsign approvals, recovery-token mints). Sensitive values are redacted before logging — substring matches on `api_key` / `password` / `secret` / `token` in the key path.

Query examples:

```sql
-- Recent admin actions
SELECT created_at, actor, action, target, details
FROM audit_logs
ORDER BY id DESC LIMIT 50;

-- Who minted recovery tokens
SELECT created_at, actor, target FROM audit_logs
WHERE action = 'recovery.token_minted';
```

## Backups

The only stateful piece is `skynetcontrol.db` (or your Postgres instance). Schedule a periodic backup:

- SQLite: `sqlite3 skynetcontrol.db ".backup '/backups/skynetcontrol-$(date +%Y%m%d).db'"`.
- Postgres: `pg_dump`.

Encrypted credentials in `app_config` round-trip through backups intact only if you also preserve the key material (`SKYNET_SECRETS_KEY`, or `SKYNET_JWT_SECRET_KEY` if you're not using a separate secrets key). A backup without the key is unrecoverable — the wizard's preserve-on-empty sentinel lets you re-enter credentials manually, but the audit-log + history are gone.
