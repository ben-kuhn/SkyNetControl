# Configuring authentication providers

SkyNetControl signs users in via OAuth 2.0 / OpenID Connect. At least one provider must be enabled before users can sign in.

**Providers are configured through the UI, not env vars.** On first boot, the wizard at `/setup` walks you through one provider. After setup, add or change providers via the **Authentication** group on the `/config` page (admin only). OAuth `client_secret` values are encrypted at rest in the database (see [secrets.md](secrets.md#at-rest-encryption)).

The callback URL for every provider follows the same pattern:

```
{APP_BASE_URL}/api/auth/callback/{provider}
```

`APP_BASE_URL` is whatever you set in `SKYNET_APP_BASE_URL` — your real public URL in production (e.g. `https://net.example.org`), or `http://localhost:5173` for local dev.

`{provider}` is one of the built-in providers (`github`, `google`, `microsoft`, `discord`, `facebook`) or the slug you choose for a Generic OIDC provider (e.g. `authentik`, `keycloak`). The bare slug `oidc` is reserved.

Step 2 of the setup wizard shows the exact callback URI to register at the IdP. Copy it from there rather than constructing by hand — it accounts for any trailing-slash / scheme drift.

---

## GitHub (simplest)

Best for personal/homelab deployments. No domain verification, no app review.

1. Go to <https://github.com/settings/developers> → **OAuth Apps** → **New OAuth App**.
2. Fill in:
   - **Application name**: `SkyNetControl` (or whatever you like)
   - **Homepage URL**: your `APP_BASE_URL` (e.g. `https://net.example.org`)
   - **Authorization callback URL**: `{APP_BASE_URL}/api/auth/callback/github`
3. After creation, copy the **Client ID**.
4. Click **Generate a new client secret**, copy it.
5. Paste both into Step 2 of the `/setup` wizard (first boot) or the Authentication group on `/config` (post-setup).

The first user to sign in becomes admin automatically.

---

## Google

1. Go to <https://console.cloud.google.com/apis/credentials>. Pick a project (or create one).
2. **Configure OAuth consent screen** if you haven't already:
   - User Type: **External** (for non-Workspace use)
   - Add your email as a test user during dev; submit for verification before opening to the public.
3. **Create credentials** → **OAuth client ID** → **Web application**.
4. Fill in:
   - **Authorized JavaScript origins**: your `APP_BASE_URL` (no path)
   - **Authorized redirect URIs**: `{APP_BASE_URL}/api/auth/callback/google`
5. Copy the **Client ID** and **Client secret**.
6. Paste both into the wizard or `/config`.

While the app is in "Testing" mode, only listed test users can sign in. Move to "Production" when you're ready to open it up.

---

## Microsoft (Azure / Entra ID)

1. Go to <https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade>.
2. **New registration**.
3. Fill in:
   - **Name**: `SkyNetControl`
   - **Supported account types**: usually **Personal Microsoft accounts only** for a homelab, or **Multitenant + personal** for broader access.
   - **Redirect URI**: select **Web**, enter `{APP_BASE_URL}/api/auth/callback/microsoft`.
4. After registration, copy the **Application (client) ID**.
5. Under **Certificates & secrets** → **New client secret**. Copy the value (not the ID).
6. Paste both into the wizard or `/config`.

---

## Discord

1. Go to <https://discord.com/developers/applications> → **New Application**.
2. Under **OAuth2** → **Redirects**, add `{APP_BASE_URL}/api/auth/callback/discord`.
3. Copy the **Client ID** from the General Information page.
4. **Reset Secret** to generate a new one; copy it.
5. Paste both into the wizard or `/config`.

---

## Facebook

1. Go to <https://developers.facebook.com/apps/> → **Create App** → **Consumer**.
2. Add the **Facebook Login** product.
3. Under **Facebook Login** → **Settings** → **Valid OAuth Redirect URIs**, add `{APP_BASE_URL}/api/auth/callback/facebook`.
4. From the app's **Settings** → **Basic**, copy the **App ID** and **App Secret**.
5. Paste both into the wizard or `/config`.

Note: Facebook requires the app to be in Live Mode (and reviewed for `email` permission) before strangers can sign in.

---

## Generic OIDC

For any OIDC-compliant provider that isn't listed above (Authentik, Keycloak, Okta, Auth0, Zitadel, …). You can configure multiple custom OIDC providers — each gets its own URL slug, friendly name, issuer URL, and OAuth credentials.

In the wizard / config page, pick **Custom OIDC** and fill in:

- **Provider name** — label shown on the login button (e.g. "My Authentik").
- **Slug** — auto-derived from the name (lowercase, hyphen-separated). The callback URL incorporates this: `{APP_BASE_URL}/api/auth/callback/<slug>`.
- **Issuer URL** — the IdP's well-known base (the URL whose `/.well-known/openid-configuration` resolves; we'll append the path automatically).
- **Client ID** and **Client Secret** — issued by the IdP.

Reserved slugs (cannot be used as custom): `google`, `github`, `microsoft`, `discord`, `facebook`, `oidc`.

### What we verify

For OIDC providers, the callback verifies the `id_token` signature against the IdP's JWKS, plus `iss`, `aud`, `exp`, and `nonce`. The signing algorithm must be RS256 / RS384 / RS512 / ES256 / ES384 / ES512 / PS256 / PS384 / PS512 — `none` and HMAC algorithms are refused. If your IdP signs with something exotic, the callback will return 401; check the server log.

The `issuer_url` you supply must use `https://` and must resolve to a globally routable IP (the SSRF guard refuses loopback, RFC1918, link-local, and cloud-metadata addresses). The same guard applies to the token, userinfo, and JWKS endpoints learned from the discovery doc.

### Two providers side-by-side

You can add as many providers as you want — each via the Authentication group on `/config` after setup. Example: Authentik and Keycloak both enabled with their own slugs (`authentik`, `keycloak`). The login page shows a button per enabled provider.

Callback URLs to register in each IdP, for `APP_BASE_URL=https://net.example.org`:

- Authentik: `https://net.example.org/api/auth/callback/authentik`
- Keycloak: `https://net.example.org/api/auth/callback/keycloak`

---

## Multiple providers

You can enable as many providers as you want simultaneously. The login page shows a button for each. Users who sign in via different providers get different accounts — the `oidc_subject` column scopes per-provider identity (e.g., `github:1234`, `google:abcd`).

A single human signing in via two providers will appear as two separate users. Plan accordingly.

---

## After credentials are set

1. Open `{APP_BASE_URL}/login`. You should see a button for each enabled provider.
2. The very first user to complete sign-in is granted `ADMIN` automatically. Every subsequent user starts as `PENDING` and must be approved by an admin via the `/users` page.

### Closing registration

The `/config` page exposes an **Open Registration** toggle (also keyed `registration_open` in `app_config`). Default is on. Toggle it off to refuse new OAuth sign-ins; existing users keep signing in. Useful for nets that batch-onboard known operators.

### If you're locked out

`skynetcontrol-recovery mint-admin-token` prints a one-time URL that enters recovery mode and lets you edit the OAuth credentials. See [operations.md → Recovery](operations.md#recovery-locked-out-of-the-admin-account).

---

## Troubleshooting

### "Unknown auth provider" or no providers on `/login`

No provider has `enabled=true` and a non-empty `client_id` in `app_config`. Re-check the Authentication group on `/config` (or rerun the wizard via the recovery flow if you're locked out).

### OAuth provider returns "redirect_uri_mismatch"

The callback URL you registered with the provider doesn't exactly match `{APP_BASE_URL}/api/auth/callback/{provider}`. Common slip-ups:

- Trailing slash (`/api/auth/callback/github/` vs `/api/auth/callback/github`).
- Wrong scheme (`http://` registered, `https://` used in production).
- Wrong host (registered with the bare domain, accessed via `www.`).

Step 2 of the setup wizard shows the canonical URI verbatim — copy from there to avoid drift.

### Callback returns 401 "id_token verification failed"

The IdP's JWKS is unreachable, or its `id_token` is signed with an unsupported algorithm, or the `iss` / `aud` / `nonce` claim doesn't match what we sent. Check `journalctl -u skynetcontrol` — the verifier logs the specific failure at WARNING.

### Callback returns 400 "Provider URL refused by SSRF guard"

The token / userinfo / JWKS URL the discovery doc declared isn't fetchable from the server: non-https scheme, or hostname resolves to a non-global IP. This protects against hostile discovery docs; if it fires on a legitimate IdP, double-check the IdP's discovery document at `{ISSUER_URL}/.well-known/openid-configuration`.

### Logged in but the SPA still asks me to log in

The cookie was set on a different origin than the SPA is on. In local dev, set `SKYNET_APP_BASE_URL=http://localhost:5173` (the Vite port) so the cookie binds correctly. See [docs/development.md](../development.md#why-skynet_app_base_urlhttplocalhost5173-in-dev).

### Provider works but the first user isn't getting admin

A user record already exists in the database from an earlier attempt. Clear it (`sqlite3 /var/lib/skynetcontrol/skynetcontrol.db "DELETE FROM users WHERE callsign LIKE 'PENDING-%';"`) and sign in again. Or just approve them manually in the running app — but you'd need an existing admin to do that.
