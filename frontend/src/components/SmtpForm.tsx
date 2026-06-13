import { useCallback, useEffect, useState } from "react";
import { clearSmtp, getSmtp, sendSmtpTest, upsertSmtp } from "../api/smtp";
import type { SmtpConfig, SmtpUpsert } from "../api/smtp";
import { Button } from "./Button";
import { Input } from "./Input";
import { Modal } from "./Modal";
import { TestResultBanner } from "./TestResultBanner";
import type { TestResult } from "./TestResultBanner";

// ─────────────────────────────────────────────────────────────────────
// Form state
// ─────────────────────────────────────────────────────────────────────

interface SmtpFormState {
  host: string;
  port: string; // keep as string in the form; convert to number on save
  username: string;
  password: string; // "" means "don't touch stored password"
  from_address: string;
  use_tls: boolean;
  clearPassword: boolean; // when true, send "-" as password to clear
}

const EMPTY_FORM: SmtpFormState = {
  host: "",
  port: "587",
  username: "",
  password: "",
  from_address: "",
  use_tls: true,
  clearPassword: false,
};

function configToForm(cfg: SmtpConfig): SmtpFormState {
  return {
    host: cfg.host,
    port: String(cfg.port),
    username: cfg.username,
    // Server always sends "***" or "" — translate to empty so we send ""
    // (preserve) unless user explicitly types something or toggles clearPassword.
    password: "",
    from_address: cfg.from_address,
    use_tls: cfg.use_tls,
    clearPassword: false,
  };
}

// What the server last returned (for dirty-checking), in normalised form.
interface SavedSnapshot {
  host: string;
  port: string;
  username: string;
  from_address: string;
  use_tls: boolean;
  // We don't compare password (server redacts it) or clearPassword.
}

function configToSnapshot(cfg: SmtpConfig): SavedSnapshot {
  return {
    host: cfg.host,
    port: String(cfg.port),
    username: cfg.username,
    from_address: cfg.from_address,
    use_tls: cfg.use_tls,
  };
}

function formToSnapshot(f: SmtpFormState): SavedSnapshot {
  return {
    host: f.host,
    port: f.port,
    username: f.username,
    from_address: f.from_address,
    use_tls: f.use_tls,
  };
}

function isDirty(form: SmtpFormState, saved: SavedSnapshot | null): boolean {
  if (saved === null) {
    // Nothing saved yet — dirty if any meaningful field is filled.
    return form.host.trim().length > 0 || form.from_address.trim().length > 0;
  }
  // Structural diff
  const cur = formToSnapshot(form);
  if (
    cur.host !== saved.host ||
    cur.port !== saved.port ||
    cur.username !== saved.username ||
    cur.from_address !== saved.from_address ||
    cur.use_tls !== saved.use_tls
  ) {
    return true;
  }
  // Password changes
  if (form.clearPassword) return true;
  if (form.password.length > 0) return true;
  return false;
}

function buildUpsert(form: SmtpFormState): SmtpUpsert {
  let password: string;
  if (form.clearPassword) {
    password = "-";
  } else if (form.password.length > 0) {
    password = form.password;
  } else {
    password = ""; // preserve stored value
  }
  return {
    host: form.host.trim(),
    port: parseInt(form.port, 10) || 587,
    username: form.username.trim(),
    password,
    from_address: form.from_address.trim(),
    use_tls: form.use_tls,
  };
}

// ─────────────────────────────────────────────────────────────────────
// Send test email modal
// ─────────────────────────────────────────────────────────────────────

interface TestModalProps {
  onClose: () => void;
  onSend: (toAddress: string) => Promise<void>;
  sending: boolean;
}

function TestEmailModal({ onClose, onSend, sending }: TestModalProps) {
  const [toAddress, setToAddress] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!toAddress.trim()) return;
    await onSend(toAddress.trim());
  };

  return (
    <Modal open onClose={onClose} title="Send test email">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <p className="text-sm text-text-secondary">
          A test message will be sent using the current form values (not the last saved values).
        </p>
        <Input
          label="Destination address"
          type="email"
          value={toAddress}
          onChange={(e) => setToAddress(e.target.value)}
          placeholder="you@example.com"
          required
          autoFocus
        />
        <div className="flex justify-end gap-2 mt-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={sending}>
            Cancel
          </Button>
          <Button type="submit" size="sm" loading={sending} disabled={!toAddress.trim()}>
            Send
          </Button>
        </div>
      </form>
    </Modal>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Password field with show/hide + clear toggle
// ─────────────────────────────────────────────────────────────────────

interface PasswordFieldProps {
  value: string;
  hasStoredPassword: boolean;
  clearPassword: boolean;
  onChange: (value: string) => void;
  onToggleClear: (clear: boolean) => void;
}

function PasswordField({ value, hasStoredPassword, clearPassword, onChange, onToggleClear }: PasswordFieldProps) {
  const [show, setShow] = useState(false);

  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-text-secondary">Password</label>
      <div className="relative">
        <input
          type={show ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={hasStoredPassword ? "(unchanged — stored password will be used)" : ""}
          disabled={clearPassword}
          className={`
            w-full rounded-md border border-border bg-bg-elevated px-3 py-2 pr-16 text-sm
            text-text-primary placeholder:text-text-muted
            focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent
            disabled:opacity-50 disabled:cursor-not-allowed
          `}
        />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary text-xs px-1"
          tabIndex={-1}
        >
          {show ? "Hide" : "Show"}
        </button>
      </div>
      {hasStoredPassword && (
        <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer mt-1">
          <input
            type="checkbox"
            checked={clearPassword}
            onChange={(e) => {
              onToggleClear(e.target.checked);
              if (e.target.checked) onChange(""); // clear text input when toggling
            }}
            className="accent-accent"
          />
          Clear stored password
        </label>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────

export function SmtpForm() {
  const [form, setForm] = useState<SmtpFormState>(EMPTY_FORM);
  const [saved, setSaved] = useState<SavedSnapshot | null>(null);
  const [hasStoredPassword, setHasStoredPassword] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testModal, setTestModal] = useState(false);
  const [testSending, setTestSending] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setLoadError(null);
    getSmtp()
      .then((cfg) => {
        if (cfg === null) {
          setForm(EMPTY_FORM);
          setSaved(null);
          setHasStoredPassword(false);
        } else {
          setForm(configToForm(cfg));
          setSaved(configToSnapshot(cfg));
          setHasStoredPassword(cfg.password === "***");
        }
      })
      .catch(() => setLoadError("Failed to load SMTP configuration."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const result = await upsertSmtp(buildUpsert(form));
      setForm(configToForm(result));
      setSaved(configToSnapshot(result));
      setHasStoredPassword(result.password === "***");
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Failed to save SMTP settings.");
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    if (!window.confirm("Clear all SMTP settings? This cannot be undone.")) return;
    try {
      await clearSmtp();
      setForm(EMPTY_FORM);
      setSaved(null);
      setHasStoredPassword(false);
    } catch {
      // Surface nothing — user can retry
    }
  };

  const handleTestSend = async (toAddress: string) => {
    setTestSending(true);
    try {
      const result = await sendSmtpTest({ ...buildUpsert(form), to_address: toAddress });
      setTestModal(false);
      setTestResult(
        result.ok
          ? { ok: true, message: `Test email sent successfully to ${toAddress}.` }
          : { ok: false, message: result.error ?? "SMTP test failed." },
      );
    } catch (err: unknown) {
      setTestModal(false);
      setTestResult({
        ok: false,
        message: err instanceof Error ? err.message : "SMTP test failed.",
      });
    } finally {
      setTestSending(false);
    }
  };

  const dirty = isDirty(form, saved);

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
      <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
        Email (SMTP)
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
      ) : (
        <form onSubmit={handleSave} className="flex flex-col gap-4">
          <div className="grid grid-cols-[1fr_auto] gap-3">
            <Input
              label="Host"
              value={form.host}
              onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
              placeholder="smtp.gmail.com"
              mono
            />
            <Input
              label="Port"
              type="number"
              value={form.port}
              onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
              placeholder="587"
              className="w-20"
            />
          </div>

          <Input
            label="Username"
            value={form.username}
            onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
            placeholder="alice@example.com"
          />

          <PasswordField
            value={form.password}
            hasStoredPassword={hasStoredPassword}
            clearPassword={form.clearPassword}
            onChange={(password) => setForm((f) => ({ ...f, password }))}
            onToggleClear={(clearPassword) => setForm((f) => ({ ...f, clearPassword }))}
          />

          <Input
            label="From address"
            value={form.from_address}
            onChange={(e) => setForm((f) => ({ ...f, from_address: e.target.value }))}
            placeholder="alice@example.com"
          />

          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={form.use_tls}
              onChange={(e) => setForm((f) => ({ ...f, use_tls: e.target.checked }))}
              className="accent-accent"
            />
            Use TLS
          </label>

          {saveError && <p className="text-sm text-danger">{saveError}</p>}

          {testResult && (
            <TestResultBanner result={testResult} onDismiss={() => setTestResult(null)} />
          )}

          <div className="flex items-center gap-2 mt-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setTestModal(true)}
              disabled={!form.host.trim()}
            >
              Send test email
            </Button>
            <div className="flex-1" />
            {saved !== null && (
              <Button type="button" variant="danger" size="sm" onClick={handleClear}>
                Clear
              </Button>
            )}
            <Button
              type="submit"
              size="sm"
              loading={saving}
              disabled={!dirty}
              variant={dirty ? "primary" : "secondary"}
            >
              Save
            </Button>
          </div>
        </form>
      )}

      {testModal && (
        <TestEmailModal
          onClose={() => setTestModal(false)}
          onSend={handleTestSend}
          sending={testSending}
        />
      )}
    </div>
  );
}
