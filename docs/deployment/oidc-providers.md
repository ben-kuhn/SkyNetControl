# Configuring authentication providers

SkyNetControl signs users in via OAuth 2.0 / OpenID Connect. At least one provider must be enabled before the backend will start.

This guide covers how to register an OAuth/OIDC app with each supported provider, then plug the resulting credentials into SkyNetControl. The callback URL for every provider follows the same pattern:

```
{APP_BASE_URL}/api/auth/callback/{provider}
```

`APP_BASE_URL` is whatever you set in `SKYNET_APP_BASE_URL` — your real public URL in production (e.g. `https://net.example.org`), or `http://localhost:5173` for local dev.

`{provider}` is one of: `github`, `google`, `microsoft`, `discord`, `facebook`, `oidc`.

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
5. In your env:
   ```
   SKYNET_AUTH_GITHUB_ENABLED=true
   SKYNET_AUTH_GITHUB_CLIENT_ID=Iv1.xxxxxxxxxxxx
   SKYNET_AUTH_GITHUB_CLIENT_SECRET=xxxxxxxxxxxx
   ```

That's it. Any GitHub user can now sign in. The first one to do so becomes admin automatically.

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
6. In your env:
   ```
   SKYNET_AUTH_GOOGLE_ENABLED=true
   SKYNET_AUTH_GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com
   SKYNET_AUTH_GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxx
   ```

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
6. In your env:
   ```
   SKYNET_AUTH_MICROSOFT_ENABLED=true
   SKYNET_AUTH_MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   SKYNET_AUTH_MICROSOFT_CLIENT_SECRET=xxxxxxxxxxxxxxx
   ```

---

## Discord

1. Go to <https://discord.com/developers/applications> → **New Application**.
2. Under **OAuth2** → **Redirects**, add `{APP_BASE_URL}/api/auth/callback/discord`.
3. Copy the **Client ID** from the General Information page.
4. **Reset Secret** to generate a new one; copy it.
5. In your env:
   ```
   SKYNET_AUTH_DISCORD_ENABLED=true
   SKYNET_AUTH_DISCORD_CLIENT_ID=xxxxxxxxxxxxxxxxx
   SKYNET_AUTH_DISCORD_CLIENT_SECRET=xxxxxxxx
   ```

---

## Facebook

1. Go to <https://developers.facebook.com/apps/> → **Create App** → **Consumer**.
2. Add the **Facebook Login** product.
3. Under **Facebook Login** → **Settings** → **Valid OAuth Redirect URIs**, add `{APP_BASE_URL}/api/auth/callback/facebook`.
4. From the app's **Settings** → **Basic**, copy the **App ID** and **App Secret**.
5. In your env:
   ```
   SKYNET_AUTH_FACEBOOK_ENABLED=true
   SKYNET_AUTH_FACEBOOK_CLIENT_ID=xxxxxxxxxxxxxxxx
   SKYNET_AUTH_FACEBOOK_CLIENT_SECRET=xxxxxxxx
   ```

Note: Facebook requires the app to be in Live Mode (and reviewed for `email` permission) before strangers can sign in.

---

## Generic OIDC (Authentik, Keycloak, Zitadel, etc.)

For any provider that speaks standard OIDC. The setup pattern is similar everywhere:

1. In your identity provider, register a new OIDC application:
   - **Client type**: confidential
   - **Redirect URI**: `{APP_BASE_URL}/api/auth/callback/oidc`
   - **Scopes**: `openid email profile` (the only scopes SkyNetControl requests)
2. Note the **Issuer URL** — the base URL of the provider's OIDC discovery endpoint. SkyNetControl appends `/.well-known/openid-configuration` automatically.
3. Get the **Client ID** and **Client secret**.
4. In your env:
   ```
   SKYNET_AUTH_OIDC_ENABLED=true
   SKYNET_AUTH_OIDC_CLIENT_ID=skynetcontrol
   SKYNET_AUTH_OIDC_CLIENT_SECRET=xxxxxxxx
   SKYNET_AUTH_OIDC_ISSUER_URL=https://auth.example.org
   ```

### Authentik

1. **Applications** → **Create**:
   - Name: `SkyNetControl`
   - Slug: `skynetcontrol`
   - Provider: create a new **OAuth2/OpenID Provider** with:
     - Client type: Confidential
     - Redirect URI (regex): `https://net.example.org/api/auth/callback/oidc` (escape dots if you want to be strict)
     - Scopes: openid, email, profile
2. Copy the Client ID and Client Secret from the provider detail.
3. The issuer URL is your Authentik base, e.g. `https://auth.example.org/application/o/skynetcontrol/`.

### Keycloak

1. In your realm, **Clients** → **Create client**:
   - Client type: OpenID Connect
   - Client ID: `skynetcontrol`
   - Client authentication: ON
   - Authentication flow: Standard flow
2. **Settings** → **Valid redirect URIs**: `{APP_BASE_URL}/api/auth/callback/oidc`
3. **Credentials** tab → copy the secret.
4. Issuer URL: `https://keycloak.example.org/realms/{realm-name}`.

### Zitadel

1. **Projects** → create a project → **New application** → **Web** → **Code** (PKCE optional).
2. Redirect URI: `{APP_BASE_URL}/api/auth/callback/oidc`.
3. Copy the Client ID and (if confidential) the Client secret.
4. Issuer URL: your Zitadel instance URL (e.g., `https://your-instance.zitadel.cloud`).

---

## Multiple providers

You can enable as many providers as you want simultaneously. The login page shows a button for each. Users who sign in via different providers get different accounts — the `oidc_subject` column scopes per-provider identity (e.g., `github:1234`, `google:abcd`).

A single human signing in via two providers will appear as two separate users. Plan accordingly.

---

## After credentials are set

1. Restart the backend so it picks up the env changes.
2. Open `{APP_BASE_URL}/login`. You should see a button for each enabled provider.
3. The very first user to complete sign-in is granted `ADMIN` automatically. Every subsequent user starts as `PENDING` and must be approved by an admin via the `/users` page.

---

## Troubleshooting

### "No auth providers are enabled" at startup

You don't have any `SKYNET_AUTH_*_ENABLED=true` env var with matching `CLIENT_ID` and `CLIENT_SECRET` values. Set them and restart.

### OAuth provider returns "redirect_uri_mismatch"

The callback URL you registered with the provider doesn't exactly match `{APP_BASE_URL}/api/auth/callback/{provider}`. Common slip-ups:

- Trailing slash (`/api/auth/callback/github/` vs `/api/auth/callback/github`).
- Wrong scheme (`http://` registered, `https://` used in production).
- Wrong host (registered with the bare domain, accessed via `www.`).

### Logged in but the SPA still asks me to log in

The cookie was set on a different origin than the SPA is on. In local dev, set `SKYNET_APP_BASE_URL=http://localhost:5173` (the Vite port) so the cookie binds correctly. See [docs/development.md](../development.md#why-skynet_app_base_urlhttplocalhost5173-in-dev).

### Provider works but the first user isn't getting admin

A user record already exists in the database from an earlier attempt. Clear it (`sqlite3 /var/lib/skynetcontrol/skynetcontrol.db "DELETE FROM users WHERE callsign = 'PENDING-xxxxx';"`) and sign in again. Or just approve them manually in the running app — but you'd need an existing admin to do that.
