import { useEffect, useState } from "react";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { startSetupClaim } from "../api/setup";
import { fetchConfig, setConfigValue } from "../api/config";
import { listOAuthProviders, upsertOAuthProvider } from "../api/oauth";
import { getSmtp, upsertSmtp, clearSmtp } from "../api/smtp";
import { clearRecoveryCookie } from "../api/recovery";
import type { SmtpUpsert } from "../api/smtp";

// Mirror of `backend/auth/oidc_slug.py:slugify` — kept client-side so the
// wizard doesn't depend on the admin-only /api/admin/oauth/providers/slug/derive
// endpoint. Backend revalidates the slug at /api/setup/claim/start anyway, so
// this is a UX preview only.
function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// ─────────────────────────────────────────────────────────────────────────────
// Provider definitions
// ─────────────────────────────────────────────────────────────────────────────

type FixedProvider = "google" | "microsoft" | "github" | "discord" | "facebook";
type ProviderType = FixedProvider | "custom_oidc";

const FIXED_PROVIDERS: Record<FixedProvider, string> = {
  google: "Google",
  microsoft: "Microsoft",
  github: "GitHub",
  discord: "Discord",
  facebook: "Facebook",
};

// ─────────────────────────────────────────────────────────────────────────────
// Form state
// ─────────────────────────────────────────────────────────────────────────────

interface SmtpFormState {
  host: string;
  port: string;
  username: string;
  password: string;
  from_address: string;
  use_tls: boolean;
}

interface WizardFormState {
  // Step 1: Net basics
  default_net_control: string;
  net_address: string;
  app_base_url: string;
  // Step 2: OAuth provider
  provider_type: ProviderType;
  oauth_slug: string;
  oauth_name: string;
  oauth_client_id: string;
  oauth_client_secret: string;
  oauth_issuer_url: string; // only for custom OIDC
  // Step 3: SMTP (null = skipped)
  smtp: SmtpFormState | null;
}

const INITIAL_FORM: WizardFormState = {
  default_net_control: "",
  net_address: "",
  app_base_url: typeof window !== "undefined" ? window.location.origin : "",
  provider_type: "google",
  oauth_slug: "google",
  oauth_name: "Google",
  oauth_client_id: "",
  oauth_client_secret: "",
  oauth_issuer_url: "",
  smtp: {
    host: "",
    port: "587",
    username: "",
    password: "",
    from_address: "",
    use_tls: true,
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Step indicator
// ─────────────────────────────────────────────────────────────────────────────

function StepIndicator({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-2 mb-6">
      {Array.from({ length: total }, (_, i) => i + 1).map((n) => (
        <div key={n} className="flex items-center gap-2">
          <div
            className={`h-7 w-7 rounded-full flex items-center justify-center text-xs font-semibold ${
              n === current
                ? "bg-accent text-bg-base"
                : n < current
                  ? "bg-accent/30 text-accent"
                  : "bg-bg-elevated text-text-muted border border-border"
            }`}
          >
            {n}
          </div>
          {n < total && <div className={`h-px w-6 ${n < current ? "bg-accent/40" : "bg-border"}`} />}
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Step 1: Net basics
// ─────────────────────────────────────────────────────────────────────────────

interface Step1Props {
  form: WizardFormState;
  setForm: React.Dispatch<React.SetStateAction<WizardFormState>>;
  onNext: () => void;
  recoveryMode: boolean;
}

function Step1({ form, setForm, onNext, recoveryMode }: Step1Props) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canAdvance =
    form.default_net_control.trim().length > 0 &&
    form.net_address.trim().length > 0 &&
    form.app_base_url.trim().length > 0;

  const handleNext = async () => {
    if (!canAdvance) return;
    if (recoveryMode) {
      setSaving(true);
      setError(null);
      try {
        await setConfigValue("default_net_control", form.default_net_control.trim());
        await setConfigValue("net_address", form.net_address.trim());
        await setConfigValue("app_base_url", form.app_base_url.trim());
        onNext();
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to save. Please try again.");
      } finally {
        setSaving(false);
      }
    } else {
      onNext();
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        handleNext();
      }}
      className="flex flex-col gap-4"
    >
      <Input
        label="Net control callsign"
        value={form.default_net_control}
        onChange={(e) => setForm((f) => ({ ...f, default_net_control: e.target.value.toUpperCase() }))}
        placeholder="W0NE"
        mono
        required
        autoFocus
      />
      <Input
        label="Net address (Winlink)"
        value={form.net_address}
        onChange={(e) => setForm((f) => ({ ...f, net_address: e.target.value }))}
        placeholder="w0ne@winlink.org"
        mono
        required
      />
      <Input
        label="App base URL"
        value={form.app_base_url}
        onChange={(e) => setForm((f) => ({ ...f, app_base_url: e.target.value }))}
        placeholder="https://net.example.com"
        mono
        required
      />
      <p className="text-xs text-text-muted">
        The base URL is used for OAuth redirect URIs. It is pre-filled from your browser's address.
      </p>
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex justify-end mt-2">
        <Button type="submit" disabled={!canAdvance || saving} loading={saving}>
          Next
        </Button>
      </div>
    </form>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Step 2: OAuth provider
// ─────────────────────────────────────────────────────────────────────────────

interface Step2Props {
  form: WizardFormState;
  setForm: React.Dispatch<React.SetStateAction<WizardFormState>>;
  onBack: () => void;
  onNext: () => void;
  recoveryMode: boolean;
  setOauthEdited: (edited: boolean) => void;
}

function Step2({ form, setForm, onBack, onNext, recoveryMode, setOauthEdited }: Step2Props) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  // Strip trailing slash so the rendered URI is clean even if the user
  // typed "https://example.com/" on Step 1.
  const baseUrl = form.app_base_url.trim().replace(/\/+$/, "");
  const slug = form.oauth_slug.trim();
  const signInUri = baseUrl && slug ? `${baseUrl}/api/auth/callback/${slug}` : "";

  const copy = async (uri: string) => {
    try {
      await navigator.clipboard.writeText(uri);
      setCopied(uri);
      window.setTimeout(() => setCopied((c) => (c === uri ? null : c)), 1500);
    } catch {
      /* clipboard blocked — user can select and copy manually */
    }
  };

  // Track any change in Step 2 inputs to set oauthEdited
  const markEdited = () => {
    if (recoveryMode) setOauthEdited(true);
  };

  // When provider type changes, update slug + name (for fixed providers)
  const handleProviderType = (type: ProviderType) => {
    markEdited();
    if (type !== "custom_oidc") {
      setForm((f) => ({
        ...f,
        provider_type: type,
        oauth_slug: type,
        oauth_name: FIXED_PROVIDERS[type as FixedProvider],
        oauth_issuer_url: "",
      }));
    } else {
      setForm((f) => ({
        ...f,
        provider_type: "custom_oidc",
        oauth_slug: "",
        oauth_name: "",
        oauth_issuer_url: "",
      }));
    }
  };

  const handleCustomName = (name: string) => {
    markEdited();
    setForm((f) => ({ ...f, oauth_name: name, oauth_slug: slugify(name) }));
  };

  const handleOAuthField = <K extends "oauth_client_id" | "oauth_client_secret" | "oauth_issuer_url">(
    field: K,
    value: string,
  ) => {
    markEdited();
    setForm((f) => ({ ...f, [field]: value }));
  };

  const isCustomOidc = form.provider_type === "custom_oidc";
  // Note: `oauthTested` is NOT in the canAdvance check. The "Test sign-in"
  // popup hits /api/admin/test/oauth/*, which is admin-gated — and during
  // setup there is no admin yet. Gating Next on a test that can't succeed
  // would brick the wizard. The actual claim flow at Step 4 IS the test;
  // if the credentials are wrong, the wizard re-renders with an error
  // page and the user can come back here to fix them.
  const canAdvance =
    form.oauth_client_id.trim().length > 0 &&
    form.oauth_slug.trim().length > 0 &&
    (!isCustomOidc || (form.oauth_issuer_url.trim().length > 0 && form.oauth_name.trim().length > 0)) &&
    // In recovery mode, secret can be empty (means preserve); in first-boot it's required
    (recoveryMode || form.oauth_client_secret.trim().length > 0);

  const handleNext = async () => {
    if (!canAdvance) return;
    if (recoveryMode) {
      setSaving(true);
      setError(null);
      try {
        await upsertOAuthProvider(form.oauth_slug, {
          name: form.oauth_name,
          enabled: true,
          client_id: form.oauth_client_id.trim(),
          client_secret: form.oauth_client_secret, // "" = preserve; user types new value to replace
          issuer_url: form.oauth_issuer_url.trim(),
        });
        onNext();
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to save OAuth provider. Please try again.");
      } finally {
        setSaving(false);
      }
    } else {
      onNext();
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        handleNext();
      }}
      className="flex flex-col gap-4"
    >
      {/* Provider type radio */}
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-text-secondary">Provider</span>
        <div className="flex flex-wrap gap-3">
          {(Object.keys(FIXED_PROVIDERS) as FixedProvider[]).map((type) => (
            <label key={type} className="flex items-center gap-1.5 text-sm text-text-secondary cursor-pointer">
              <input
                type="radio"
                name="provider_type"
                value={type}
                checked={form.provider_type === type}
                onChange={() => handleProviderType(type)}
                className="accent-accent"
              />
              {FIXED_PROVIDERS[type]}
            </label>
          ))}
          <label className="flex items-center gap-1.5 text-sm text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="provider_type"
              value="custom_oidc"
              checked={form.provider_type === "custom_oidc"}
              onChange={() => handleProviderType("custom_oidc")}
              className="accent-accent"
            />
            Custom OIDC
          </label>
        </div>
      </div>

      {/* Custom OIDC: name + issuer */}
      {isCustomOidc && (
        <>
          <Input
            label="Provider name"
            value={form.oauth_name}
            onChange={(e) => handleCustomName(e.target.value)}
            placeholder="My Identity Provider"
            required
          />
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted">Slug:</span>
            <span className="text-xs font-mono text-text-secondary">{form.oauth_slug || "—"}</span>
          </div>
          <Input
            label="Issuer URL"
            value={form.oauth_issuer_url}
            onChange={(e) => handleOAuthField("oauth_issuer_url", e.target.value)}
            placeholder="https://accounts.example.com"
            mono
            required
          />
        </>
      )}

      {/* Client credentials — always shown */}
      <Input
        label="Client ID"
        value={form.oauth_client_id}
        onChange={(e) => handleOAuthField("oauth_client_id", e.target.value)}
        placeholder="your-client-id"
        mono
        required
      />
      <Input
        label="Client Secret"
        type="password"
        value={form.oauth_client_secret}
        onChange={(e) => handleOAuthField("oauth_client_secret", e.target.value)}
        placeholder={recoveryMode ? "(unchanged — leave blank to keep existing secret)" : "your-client-secret"}
        mono
        required={!recoveryMode}
      />

      {/* The redirect URI to register at the IdP. The wizard's Step 4 claim
          uses this same URI (dispatched on the in-memory setup-session
          state), so operators only need to register one. */}
      {signInUri && (
        <div className="rounded-lg border border-border bg-bg-elevated p-3 flex flex-col gap-2 text-sm">
          <div className="text-xs font-medium text-text-muted uppercase tracking-wider">
            Redirect URI to register at your provider
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 break-all font-mono text-xs text-text-primary">{signInUri}</code>
            <Button type="button" size="sm" variant="secondary" onClick={() => copy(signInUri)}>
              {copied === signInUri ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
      )}

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex items-center justify-end mt-2 gap-2">
        <Button type="button" variant="secondary" onClick={onBack}>
          Back
        </Button>
        <Button type="submit" disabled={!canAdvance || saving} loading={saving}>
          Next
        </Button>
      </div>
    </form>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Step 3: SMTP (skippable)
// ─────────────────────────────────────────────────────────────────────────────

interface Step3Props {
  form: WizardFormState;
  setForm: React.Dispatch<React.SetStateAction<WizardFormState>>;
  onBack: () => void;
  onSkip: () => void;
  onNext: () => void;
  recoveryMode: boolean;
  hadExistingSmtp: boolean;
}

function Step3({ form, setForm, onBack, onSkip, onNext, recoveryMode, hadExistingSmtp }: Step3Props) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const smtp = form.smtp ?? { host: "", port: "587", username: "", password: "", from_address: "", use_tls: true };

  const setSmtp = (updater: (prev: SmtpFormState) => SmtpFormState) => {
    setForm((f) => ({ ...f, smtp: updater(f.smtp ?? smtp) }));
  };

  const canAdvance =
    smtp.host.trim().length > 0 &&
    smtp.from_address.trim().length > 0;

  const handleSkip = async () => {
    if (recoveryMode && hadExistingSmtp) {
      setSaving(true);
      setError(null);
      try {
        await clearSmtp();
        setForm((f) => ({ ...f, smtp: null }));
        onSkip();
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to clear SMTP. Please try again.");
      } finally {
        setSaving(false);
      }
    } else {
      setForm((f) => ({ ...f, smtp: null }));
      onSkip();
    }
  };

  const handleNext = async () => {
    if (!canAdvance) return;
    if (recoveryMode) {
      setSaving(true);
      setError(null);
      try {
        await upsertSmtp({
          host: smtp.host.trim(),
          port: parseInt(smtp.port, 10) || 587,
          username: smtp.username.trim(),
          password: smtp.password, // "" = preserve; user types new value to replace
          from_address: smtp.from_address.trim(),
          use_tls: smtp.use_tls,
        });
        onNext();
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to save SMTP. Please try again.");
      } finally {
        setSaving(false);
      }
    } else {
      onNext();
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        handleNext();
      }}
      className="flex flex-col gap-4"
    >
      <p className="text-sm text-text-secondary">
        SMTP lets SkyNetControl send reminder emails. You can skip this now and configure it later from the admin
        settings.
      </p>

      <div className="grid grid-cols-[1fr_auto] gap-3">
        <Input
          label="Host"
          value={smtp.host}
          onChange={(e) => setSmtp((s) => ({ ...s, host: e.target.value }))}
          placeholder="smtp.gmail.com"
          mono
        />
        <Input
          label="Port"
          type="number"
          value={smtp.port}
          onChange={(e) => setSmtp((s) => ({ ...s, port: e.target.value }))}
          placeholder="587"
          className="w-20"
        />
      </div>

      <Input
        label="Username"
        value={smtp.username}
        onChange={(e) => setSmtp((s) => ({ ...s, username: e.target.value }))}
        placeholder="alice@example.com"
      />

      <Input
        label="Password"
        type="password"
        value={smtp.password}
        onChange={(e) => setSmtp((s) => ({ ...s, password: e.target.value }))}
        placeholder={recoveryMode ? "(unchanged — leave blank to keep existing password)" : "app password or SMTP password"}
      />

      <Input
        label="From address"
        value={smtp.from_address}
        onChange={(e) => setSmtp((s) => ({ ...s, from_address: e.target.value }))}
        placeholder="net@example.com"
      />

      <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
        <input
          type="checkbox"
          checked={smtp.use_tls}
          onChange={(e) => setSmtp((s) => ({ ...s, use_tls: e.target.checked }))}
          className="accent-accent"
        />
        Use TLS
      </label>

      {/*
       * Note: In setup mode we intentionally skip the "Send test email" button.
       * The /api/admin/test/smtp endpoint requires admin auth, which the wizard
       * doesn't have yet (the admin user is only created on step 4's OAuth
       * callback). Adding a setup-only unauthenticated SMTP test endpoint would
       * expose an open email relay; the tradeoff isn't worth it for a one-time
       * setup wizard. Users can test SMTP from /config after setup completes.
       */}
      <p className="text-xs text-text-muted">
        SMTP can be tested from the admin config page after setup completes.
      </p>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex items-center justify-between mt-2 gap-2">
        <Button type="button" variant="secondary" onClick={onBack} disabled={saving}>
          Back
        </Button>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={handleSkip}
            disabled={saving}
          >
            Skip
          </Button>
          <Button type="submit" disabled={!canAdvance || saving} loading={saving}>
            Next
          </Button>
        </div>
      </div>
    </form>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Step 4: Claim admin (first-boot) / Review (recovery)
// ─────────────────────────────────────────────────────────────────────────────

interface Step4Props {
  form: WizardFormState;
  onBack: () => void;
  recoveryMode: boolean;
  oauthEdited: boolean;
}

function Step4({ form, onBack, recoveryMode, oauthEdited }: Step4Props) {
  const [claiming, setClaiming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const providerLabel =
    form.provider_type === "custom_oidc" ? form.oauth_name : FIXED_PROVIDERS[form.provider_type as FixedProvider];

  const handleClaim = async () => {
    setClaiming(true);
    setError(null);
    try {
      // Build SmtpUpsert from SmtpFormState if SMTP was configured
      let smtpPayload: SmtpUpsert | null = null;
      if (form.smtp !== null) {
        smtpPayload = {
          host: form.smtp.host.trim(),
          port: parseInt(form.smtp.port, 10) || 587,
          username: form.smtp.username.trim(),
          password: form.smtp.password, // fresh value — no preserve/clear sentinel needed in setup
          from_address: form.smtp.from_address.trim(),
          use_tls: form.smtp.use_tls,
        };
      }
      const { authorize_url } = await startSetupClaim({
        default_net_control: form.default_net_control.trim(),
        net_address: form.net_address.trim(),
        app_base_url: form.app_base_url.trim(),
        oauth_slug: form.oauth_slug,
        oauth_name: form.oauth_name,
        oauth_client_id: form.oauth_client_id.trim(),
        oauth_client_secret: form.oauth_client_secret,
        oauth_issuer_url: form.oauth_issuer_url.trim(),
        smtp: smtpPayload,
      });
      window.location.href = authorize_url;
    } catch (e: unknown) {
      setClaiming(false);
      setError(e instanceof Error ? e.message : "Failed to start setup. Please try again.");
    }
  };

  const handleRecoveryDone = async () => {
    try {
      await clearRecoveryCookie();
    } finally {
      // Even if the server-side logout fails, redirect to / so the SetupGate
      // gets a chance to re-check status. If the cookie was somehow not cleared,
      // it'll expire on its own in 30 minutes.
      window.location.href = "/";
    }
  };

  const handleRecoveryVerify = () => {
    window.location.href = `/api/auth/login/${form.oauth_slug}`;
  };

  if (recoveryMode) {
    return (
      <div className="flex flex-col gap-5">
        <div className="rounded-lg border border-border bg-bg-elevated p-4 flex flex-col gap-2 text-sm">
          <h3 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-1">Summary</h3>
          <div className="flex gap-3">
            <span className="text-text-muted w-36 shrink-0">Net control</span>
            <span className="text-text-primary font-mono">{form.default_net_control}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-text-muted w-36 shrink-0">Net address</span>
            <span className="text-text-primary font-mono">{form.net_address}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-text-muted w-36 shrink-0">Base URL</span>
            <span className="text-text-primary font-mono">{form.app_base_url}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-text-muted w-36 shrink-0">OAuth provider</span>
            <span className="text-text-primary">{providerLabel}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-text-muted w-36 shrink-0">SMTP</span>
            <span className={form.smtp ? "text-text-primary" : "text-text-muted italic"}>
              {form.smtp ? form.smtp.host : "skipped"}
            </span>
          </div>
        </div>

        <p className="text-sm text-text-secondary">
          {oauthEdited
            ? `Changes saved. Sign in with ${providerLabel} to verify the updated credentials.`
            : "Changes saved. Click Done to exit recovery mode."}
        </p>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex items-center justify-between gap-2">
          <Button type="button" variant="secondary" onClick={onBack}>
            Back
          </Button>
          {oauthEdited ? (
            <Button onClick={handleRecoveryVerify}>
              Sign in to verify {providerLabel}
            </Button>
          ) : (
            <Button onClick={handleRecoveryDone}>
              Done
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-lg border border-border bg-bg-elevated p-4 flex flex-col gap-2 text-sm">
        <h3 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-1">Summary</h3>
        <div className="flex gap-3">
          <span className="text-text-muted w-36 shrink-0">Net control</span>
          <span className="text-text-primary font-mono">{form.default_net_control}</span>
        </div>
        <div className="flex gap-3">
          <span className="text-text-muted w-36 shrink-0">Net address</span>
          <span className="text-text-primary font-mono">{form.net_address}</span>
        </div>
        <div className="flex gap-3">
          <span className="text-text-muted w-36 shrink-0">Base URL</span>
          <span className="text-text-primary font-mono">{form.app_base_url}</span>
        </div>
        <div className="flex gap-3">
          <span className="text-text-muted w-36 shrink-0">OAuth provider</span>
          <span className="text-text-primary">{providerLabel}</span>
        </div>
        <div className="flex gap-3">
          <span className="text-text-muted w-36 shrink-0">SMTP</span>
          <span className={form.smtp ? "text-text-primary" : "text-text-muted italic"}>
            {form.smtp ? form.smtp.host : "skipped"}
          </span>
        </div>
      </div>

      <p className="text-sm text-text-secondary">
        Clicking the button below will redirect you to <strong>{providerLabel}</strong> to sign in. On success, your
        account will become the first admin and the app will be fully configured.
      </p>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex items-center justify-between gap-2">
        <Button type="button" variant="secondary" onClick={onBack} disabled={claiming}>
          Back
        </Button>
        <Button onClick={handleClaim} loading={claiming} disabled={claiming}>
          Sign in with {providerLabel} and finish setup
        </Button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main wizard component
// ─────────────────────────────────────────────────────────────────────────────

interface SetupPageProps {
  recoveryMode?: boolean;
}

export function SetupPage({ recoveryMode = false }: SetupPageProps) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [form, setForm] = useState<WizardFormState>(INITIAL_FORM);
  const [oauthEdited, setOauthEdited] = useState(false);
  const [hadExistingSmtp, setHadExistingSmtp] = useState(false);

  // Pre-fill from existing config when in recovery mode
  useEffect(() => {
    if (!recoveryMode) return;

    async function prefill() {
      try {
        // Step 1: net basics from flat config
        const config = await fetchConfig();
        setForm((f) => ({
          ...f,
          default_net_control: config["default_net_control"] ?? f.default_net_control,
          net_address: config["net_address"] ?? f.net_address,
          app_base_url: config["app_base_url"] ?? f.app_base_url,
        }));
      } catch {
        // ignore; form keeps defaults
      }

      try {
        // Step 2: OAuth — pre-fill from first enabled provider
        const providers = await listOAuthProviders();
        const first = providers.find((p) => p.enabled) ?? providers[0];
        if (first) {
          const providerType: ProviderType = (Object.keys(FIXED_PROVIDERS) as FixedProvider[]).includes(
            first.slug as FixedProvider,
          )
            ? (first.slug as FixedProvider)
            : "custom_oidc";
          setForm((f) => ({
            ...f,
            provider_type: providerType,
            oauth_slug: first.slug,
            oauth_name: first.name,
            oauth_client_id: first.client_id,
            oauth_client_secret: "", // server returns "***" or ""; keep empty = preserve
            oauth_issuer_url: first.issuer_url,
          }));
        }
      } catch {
        // ignore
      }

      try {
        // Step 3: SMTP
        const smtp = await getSmtp();
        if (smtp) {
          setHadExistingSmtp(true);
          setForm((f) => ({
            ...f,
            smtp: {
              host: smtp.host,
              port: String(smtp.port),
              username: smtp.username,
              password: "", // server returns "***" or ""; keep empty = preserve
              from_address: smtp.from_address,
              use_tls: smtp.use_tls,
            },
          }));
        }
      } catch {
        // ignore
      }
    }

    prefill();
  }, [recoveryMode]);

  const stepTitles: Record<1 | 2 | 3 | 4, string> = {
    1: "Net basics",
    2: "OAuth provider",
    3: "Email (SMTP)",
    4: recoveryMode ? "Review & finish" : "Claim admin",
  };

  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center px-4 py-12">
      <div className="w-full max-w-2xl">
        {/* App name / logo area */}
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-text-primary">SkyNetControl</h1>
          <p className="text-sm text-text-muted mt-1">
            {recoveryMode ? "Recovery mode" : "First-boot setup wizard"}
          </p>
        </div>

        {/* Card */}
        <div className="bg-bg-surface border border-border rounded-xl shadow-lg p-8">
          {/* Recovery mode banner */}
          {recoveryMode && (
            <div className="mb-5 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning">
              Recovery mode — editing the existing configuration. Changes save per step.
            </div>
          )}

          <div className="flex items-center justify-between mb-2">
            <h2 className="text-lg font-semibold text-text-primary">
              Step {step} of 4 — {stepTitles[step]}
            </h2>
          </div>

          <StepIndicator current={step} total={4} />

          {step === 1 && (
            <Step1
              form={form}
              setForm={setForm}
              onNext={() => setStep(2)}
              recoveryMode={recoveryMode}
            />
          )}

          {step === 2 && (
            <Step2
              form={form}
              setForm={setForm}
              onBack={() => setStep(1)}
              onNext={() => setStep(3)}
              recoveryMode={recoveryMode}
              setOauthEdited={setOauthEdited}
            />
          )}

          {step === 3 && (
            <Step3
              form={form}
              setForm={setForm}
              onBack={() => setStep(2)}
              onSkip={() => setStep(4)}
              onNext={() => setStep(4)}
              recoveryMode={recoveryMode}
              hadExistingSmtp={hadExistingSmtp}
            />
          )}

          {step === 4 && (
            <Step4
              form={form}
              onBack={() => setStep(3)}
              recoveryMode={recoveryMode}
              oauthEdited={oauthEdited}
            />
          )}
        </div>
      </div>
    </div>
  );
}
