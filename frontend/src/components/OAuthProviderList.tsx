import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteOAuthProvider,
  deriveSlug,
  listOAuthProviders,
  upsertOAuthProvider,
} from "../api/oauth";
import type { OAuthProvider, OAuthProviderUpsert } from "../api/oauth";
import { Button } from "./Button";
import { Input } from "./Input";
import { Modal } from "./Modal";
import { OAuthTestButton } from "./OAuthTestButton";
import { TestResultBanner } from "./TestResultBanner";
import type { TestResult } from "./TestResultBanner";

// ─────────────────────────────────────────────────────────────────────
// Fixed providers
// ─────────────────────────────────────────────────────────────────────

interface FixedProvider {
  slug: string;
  label: string;
}

const FIXED_PROVIDERS: FixedProvider[] = [
  { slug: "google", label: "Google" },
  { slug: "microsoft", label: "Microsoft" },
  { slug: "github", label: "GitHub" },
  { slug: "discord", label: "Discord" },
  { slug: "facebook", label: "Facebook" },
];

const FIXED_SLUGS = new Set(FIXED_PROVIDERS.map((p) => p.slug));

// ─────────────────────────────────────────────────────────────────────
// Provider form state
// ─────────────────────────────────────────────────────────────────────

interface ProviderFormState {
  name: string;
  enabled: boolean;
  client_id: string;
  client_secret: string;
  issuer_url: string;
  slug: string; // editable only on Add + Custom OIDC
}

const EMPTY_FORM: ProviderFormState = {
  name: "",
  enabled: true,
  client_id: "",
  client_secret: "",
  issuer_url: "",
  slug: "",
};

// ─────────────────────────────────────────────────────────────────────
// Add modal – type picker step
// ─────────────────────────────────────────────────────────────────────

type ProviderTypeChoice = string; // slug or "custom"

interface TypePickerProps {
  onPick: (choice: ProviderTypeChoice) => void;
  onClose: () => void;
}

function TypePicker({ onPick, onClose }: TypePickerProps) {
  const [selected, setSelected] = useState<ProviderTypeChoice>("google");

  return (
    <Modal open onClose={onClose} title="Add provider — choose type">
      <div className="flex flex-col gap-3">
        {FIXED_PROVIDERS.map((fp) => (
          <label key={fp.slug} className="flex items-center gap-3 cursor-pointer">
            <input
              type="radio"
              name="provider-type"
              value={fp.slug}
              checked={selected === fp.slug}
              onChange={() => setSelected(fp.slug)}
              className="accent-accent"
            />
            <span className="text-sm text-text-primary">{fp.label}</span>
          </label>
        ))}
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="radio"
            name="provider-type"
            value="custom"
            checked={selected === "custom"}
            onChange={() => setSelected("custom")}
            className="accent-accent"
          />
          <span className="text-sm text-text-primary">Custom OIDC</span>
        </label>
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => onPick(selected)}>
            Next
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Add / Edit form modal
// ─────────────────────────────────────────────────────────────────────

interface ProviderFormModalProps {
  mode: "add" | "edit";
  /** For add: the chosen type slug or "custom". For edit: the existing provider. */
  typeChoice?: ProviderTypeChoice; // add only
  existing?: OAuthProvider; // edit only
  onSave: (slug: string, body: OAuthProviderUpsert) => Promise<void>;
  onClose: () => void;
}

function ProviderFormModal({ mode, typeChoice, existing, onSave, onClose }: ProviderFormModalProps) {
  const isEdit = mode === "edit";
  const isCustom = isEdit
    ? existing !== undefined && !FIXED_SLUGS.has(existing.slug)
    : typeChoice === "custom";

  // Determine initial form state
  const [form, setForm] = useState<ProviderFormState>(() => {
    if (isEdit && existing) {
      return {
        name: existing.name,
        enabled: existing.enabled,
        client_id: existing.client_id,
        client_secret: "", // blank = preserve
        issuer_url: existing.issuer_url,
        slug: existing.slug,
      };
    }
    // Add mode
    if (typeChoice === "custom") {
      return { ...EMPTY_FORM };
    }
    // Fixed provider
    const fp = FIXED_PROVIDERS.find((p) => p.slug === typeChoice);
    return {
      ...EMPTY_FORM,
      slug: fp?.slug ?? "",
      name: fp?.label ?? "",
    };
  });

  const [slugError, setSlugError] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Debounced slug derivation (Custom OIDC add only)
  const deriveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleNameChange = useCallback(
    (name: string) => {
      setForm((f) => ({ ...f, name }));
      if (!isEdit && isCustom) {
        if (deriveTimer.current) clearTimeout(deriveTimer.current);
        deriveTimer.current = setTimeout(async () => {
          if (!name.trim()) return;
          try {
            const result = await deriveSlug(name);
            setForm((f) => ({ ...f, slug: result.slug }));
            setSlugError(result.valid ? undefined : result.error);
          } catch {
            // ignore derive errors
          }
        }, 300);
      }
    },
    [isEdit, isCustom],
  );

  const handleSlugChange = (slug: string) => {
    setForm((f) => ({ ...f, slug }));
    setSlugError(undefined);
  };

  const isSlugValid = !isCustom || isEdit || (!slugError && form.slug.trim().length > 0);
  const canSave = !submitting && form.name.trim() && form.client_id.trim() && isSlugValid;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSave) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const body: OAuthProviderUpsert = {
        name: form.name,
        enabled: form.enabled,
        client_id: form.client_id,
        client_secret: form.client_secret, // "" = preserve on edit
        issuer_url: form.issuer_url,
      };
      await onSave(form.slug, body);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save provider.";
      setSubmitError(msg);
      if (msg.toLowerCase().includes("slug")) {
        setSlugError(msg);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const title = isEdit ? `Edit ${existing?.name ?? "provider"}` : "Add provider";

  return (
    <Modal open onClose={onClose} title={title}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* Slug — only shown for custom OIDC; read-only on Edit */}
        {isCustom && (
          <Input
            label="Slug"
            value={form.slug}
            onChange={(e) => handleSlugChange(e.target.value)}
            placeholder="e.g. pocketid"
            mono
            error={slugError}
            disabled={isEdit}
            readOnly={isEdit}
          />
        )}

        <Input
          label="Display name"
          value={form.name}
          onChange={(e) => handleNameChange(e.target.value)}
          placeholder="e.g. Google"
          required
        />

        <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            className="accent-accent"
          />
          Enabled
        </label>

        <Input
          label="Client ID"
          value={form.client_id}
          onChange={(e) => setForm((f) => ({ ...f, client_id: e.target.value }))}
          placeholder="your-client-id"
          mono
          required
        />

        <Input
          label="Client Secret"
          value={form.client_secret}
          onChange={(e) => setForm((f) => ({ ...f, client_secret: e.target.value }))}
          type="password"
          placeholder={isEdit ? "(unchanged)" : "(none — required for new providers)"}
          mono
        />

        {/* Issuer URL — Custom OIDC only, or when existing has one */}
        {(isCustom || (isEdit && existing?.issuer_url)) && (
          <Input
            label="Issuer URL"
            value={form.issuer_url}
            onChange={(e) => setForm((f) => ({ ...f, issuer_url: e.target.value }))}
            placeholder="https://your-oidc-provider.example.com"
          />
        )}

        {submitError && !slugError && (
          <p className="text-sm text-danger">{submitError}</p>
        )}

        <div className="flex justify-end gap-2 mt-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" size="sm" loading={submitting} disabled={!canSave}>
            {isEdit ? "Save" : "Add"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Provider row
// ─────────────────────────────────────────────────────────────────────

interface ProviderRowProps {
  provider: OAuthProvider;
  onToggle: (slug: string, enabled: boolean) => void;
  onEdit: (provider: OAuthProvider) => void;
  onDelete: (slug: string) => void;
  onTestResult: (r: TestResult) => void;
}

function ProviderRow({ provider, onToggle, onEdit, onDelete, onTestResult }: ProviderRowProps) {
  const hasIssuer = Boolean(provider.issuer_url);

  const handleDelete = () => {
    if (window.confirm(`Delete provider "${provider.name}"? This cannot be undone.`)) {
      onDelete(provider.slug);
    }
  };

  return (
    <div className="flex items-start gap-3 py-3 border-b border-border last:border-b-0">
      {/* Enabled toggle */}
      <input
        type="checkbox"
        checked={provider.enabled}
        onChange={(e) => onToggle(provider.slug, e.target.checked)}
        className="mt-1 accent-accent shrink-0"
        title={provider.enabled ? "Enabled — click to disable" : "Disabled — click to enable"}
      />

      {/* Name + issuer */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-text-primary">{provider.name}</p>
        {hasIssuer && (
          <p className="text-xs text-text-muted truncate">{provider.issuer_url}</p>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 shrink-0">
        <Button size="sm" variant="secondary" onClick={() => onEdit(provider)}>
          Edit
        </Button>
        <OAuthTestButton
          slug={provider.slug}
          formValues={{
            client_id: provider.client_id,
            client_secret: "", // server-side will use stored secret
            issuer_url: provider.issuer_url,
            name: provider.name,
          }}
          onResult={onTestResult}
        />
        <Button size="sm" variant="danger" onClick={handleDelete}>
          Delete
        </Button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────

type ModalState =
  | { kind: "closed" }
  | { kind: "type-picker" }
  | { kind: "add"; typeChoice: ProviderTypeChoice }
  | { kind: "edit"; provider: OAuthProvider };

export function OAuthProviderList() {
  const [providers, setProviders] = useState<OAuthProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>({ kind: "closed" });
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setLoadError(null);
    listOAuthProviders()
      .then(setProviders)
      .catch(() => setLoadError("Failed to load authentication providers."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleToggle = async (slug: string, enabled: boolean) => {
    // Optimistic update
    setProviders((prev) => prev.map((p) => (p.slug === slug ? { ...p, enabled } : p)));
    try {
      const current = providers.find((p) => p.slug === slug);
      if (!current) return;
      await upsertOAuthProvider(slug, {
        name: current.name,
        enabled,
        client_id: current.client_id,
        client_secret: "", // preserve existing secret
        issuer_url: current.issuer_url,
      });
    } catch {
      // Revert on failure
      setProviders((prev) => prev.map((p) => (p.slug === slug ? { ...p, enabled: !enabled } : p)));
    }
  };

  const handleDelete = async (slug: string) => {
    try {
      await deleteOAuthProvider(slug);
      setProviders((prev) => prev.filter((p) => p.slug !== slug));
    } catch {
      // Surface nothing — user can retry
    }
  };

  const handleSave = async (slug: string, body: OAuthProviderUpsert) => {
    await upsertOAuthProvider(slug, body);
    setModal({ kind: "closed" });
    load();
  };

  const closeModal = () => setModal({ kind: "closed" });

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
      <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
        Authentication
      </h2>

      {loadError && (
        <p className="text-sm text-danger mb-3">
          {loadError}{" "}
          <button onClick={load} className="underline hover:no-underline">
            Retry
          </button>
        </p>
      )}

      {loading ? (
        <p className="text-sm text-text-muted">Loading…</p>
      ) : providers.length === 0 ? (
        <p className="text-sm text-text-muted mb-3">No authentication providers configured.</p>
      ) : (
        <div className="mb-3">
          {providers.map((p) => (
            <ProviderRow
              key={p.slug}
              provider={p}
              onToggle={handleToggle}
              onEdit={(prov) => setModal({ kind: "edit", provider: prov })}
              onDelete={handleDelete}
              onTestResult={setTestResult}
            />
          ))}
        </div>
      )}

      {testResult && (
        <div className="mb-3">
          <TestResultBanner result={testResult} onDismiss={() => setTestResult(null)} />
        </div>
      )}

      <div className="flex justify-end">
        <Button size="sm" onClick={() => setModal({ kind: "type-picker" })}>
          + Add provider
        </Button>
      </div>

      {/* Type picker modal */}
      {modal.kind === "type-picker" && (
        <TypePicker
          onPick={(choice) => setModal({ kind: "add", typeChoice: choice })}
          onClose={closeModal}
        />
      )}

      {/* Add form modal */}
      {modal.kind === "add" && (
        <ProviderFormModal
          mode="add"
          typeChoice={modal.typeChoice}
          onSave={handleSave}
          onClose={closeModal}
        />
      )}

      {/* Edit form modal */}
      {modal.kind === "edit" && (
        <ProviderFormModal
          mode="edit"
          existing={modal.provider}
          onSave={handleSave}
          onClose={closeModal}
        />
      )}
    </div>
  );
}
