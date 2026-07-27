import { useEffect, useState } from "react";
import { useToast } from "../context/ToastContext";
import { fetchConfig, setConfigBulk } from "../api/config";
import { getFormsStatus, fetchFormsLibrary } from "../api/forms";
import type { FormsStatus } from "../api/forms";
import { Button } from "../components/Button";
import { OAuthProviderList } from "../components/OAuthProviderList";
import { SettingsSection } from "../components/SettingsSection";
import type { ConfigField } from "../components/SettingsSection";
import { SmtpForm } from "../components/SmtpForm";
import { Spinner } from "../components/Spinner";

const CALLBOOK_PROVIDER_OPTIONS = [
  { value: "hamqth", label: "HamQTH" },
  { value: "qrz", label: "QRZ" },
];

function parseStringArray(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

// Keys that the backend masks as "***" — must never be written back as "***"
const GLOBAL_SECRET_KEYS = new Set(["pat_http_password", "pat_http_token"]);

/** When a secret field loads as "***", initialize it as "" so the UI shows the placeholder. */
function initializeGlobalConfig(raw: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = { ...raw };
  for (const k of GLOBAL_SECRET_KEYS) {
    if (out[k] === "***") {
      out[k] = "";
    }
  }
  return out;
}

const AUTH_FIELDS: ConfigField[] = [
  {
    key: "registration_open",
    label: "Open Registration",
    type: "boolean",
    helpText:
      "When off, new OAuth sign-ins are refused (existing users still sign in). Turn off to prevent drive-by sign-ups from filling the database with pending rows.",
  },
];

const INTEGRATIONS_FIELDS: ConfigField[] = [
  {
    key: "claude_api_key",
    label: "Claude API Key",
    placeholder: "sk-ant-...",
    helpText: "API key for Claude-powered activity brainstorming (optional)",
    secret: true,
  },
  {
    key: "claude_daily_user_message_limit",
    label: "Claude Daily Per-User Message Limit",
    placeholder: "25",
    helpText:
      "Max brainstorm-chat messages each operator may send per UTC day. 0 = unlimited. Default 25.",
  },
  {
    key: "claude_daily_global_message_limit",
    label: "Claude Daily Global Message Limit",
    placeholder: "100",
    helpText:
      "Max brainstorm-chat messages across all operators per UTC day. 0 = unlimited. Default 100.",
  },
];

const DELIVERY_GLOBAL_FIELDS: ConfigField[] = [
  {
    key: "delivery.groupsio.api_key",
    label: "Groups.io API Key",
    placeholder: "your-api-key",
    helpText: "API key for posting to groups.io. Shared across all nets that deliver via groups.io.",
    secret: true,
  },
];

const CALLBOOK_FIELDS: ConfigField[] = [
  {
    key: "callbook.providers",
    label: "Enabled Callbook Providers",
    type: "multiselect",
    options: CALLBOOK_PROVIDER_OPTIONS,
    helpText:
      "Providers tried in order when a check-in needs name/city resolution. Leave empty to disable callbook lookup.",
  },
  {
    key: "callbook.hamqth.username",
    label: "HamQTH Username",
    placeholder: "yourcall",
    helpText: "HamQTH.com login (the callsign you registered with)",
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("hamqth"),
  },
  {
    key: "callbook.hamqth.password",
    label: "HamQTH Password",
    placeholder: "",
    helpText: "HamQTH.com account password",
    secret: true,
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("hamqth"),
  },
  {
    key: "callbook.qrz.username",
    label: "QRZ Username",
    placeholder: "yourcall",
    helpText: "QRZ.com login (paid XML subscription required for lookups)",
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("qrz"),
  },
  {
    key: "callbook.qrz.password",
    label: "QRZ Password",
    placeholder: "",
    helpText: "QRZ.com account password",
    secret: true,
    visibleWhen: (v) => parseStringArray(v["callbook.providers"] ?? "").includes("qrz"),
  },
];

const PAT_DEFAULT_BASE_FIELDS: ConfigField[] = [
  {
    key: "pat_http_base_url",
    label: "PAT Base URL",
    placeholder: "http://shack:8080",
    mono: true,
    helpText: "Base URL of your PAT HTTP API (e.g. http://localhost:8080). Used as the global default for net-free events.",
  },
  {
    key: "pat_mailbox_path",
    label: "PAT Mailbox Path",
    placeholder: "~/.local/share/pat/mailbox/YOURCALL",
    helpText: "Local filesystem path to the PAT Winlink client mailbox directory.",
    mono: true,
  },
  {
    key: "pat_transport_enabled",
    label: "PAT HTTP Transport",
    type: "boolean",
    helpText: "Enable outbound delivery via a running PAT Winlink client over its HTTP API (global default).",
  },
];

const PAT_DEFAULT_ADVANCED_FIELDS: ConfigField[] = [
  {
    key: "pat_http_auth_mode",
    label: "Auth Mode",
    type: "select",
    options: [
      { value: "none", label: "None" },
      { value: "basic", label: "Basic (username + password)" },
      { value: "token", label: "Bearer token" },
    ],
    helpText: "Authentication method for the PAT HTTP API.",
  },
  {
    key: "pat_http_username",
    label: "Username",
    placeholder: "pat",
    helpText: "Username for Basic auth.",
    visibleWhen: (v) => v["pat_http_auth_mode"] === "basic",
  },
  {
    key: "pat_http_password",
    label: "Password",
    placeholder: "leave blank to keep existing",
    secret: true,
    helpText: "Password for Basic auth. Write-only — leave blank to keep the stored value.",
    visibleWhen: (v) => v["pat_http_auth_mode"] === "basic",
  },
  {
    key: "pat_http_token",
    label: "Bearer Token",
    placeholder: "leave blank to keep existing",
    secret: true,
    helpText: "Token for Bearer auth. Write-only — leave blank to keep the stored value.",
    visibleWhen: (v) => v["pat_http_auth_mode"] === "token",
  },
  {
    key: "pat_http_timeout_seconds",
    label: "Timeout (seconds)",
    placeholder: "15",
    helpText: "HTTP request timeout for PAT API calls.",
  },
];

const APRS_DEFAULT_FIELDS: ConfigField[] = [
  {
    key: "aprs.server",
    label: "APRS-IS Server",
    placeholder: "rotate.aprs2.net",
    mono: true,
    helpText: "Default APRS-IS server for net-free events. Leave default unless you run your own server.",
  },
  {
    key: "aprs.port",
    label: "APRS-IS Port",
    placeholder: "14580",
    helpText: "Default filtered-feed port for net-free events.",
  },
];

const WEATHER_DEFAULT_FIELDS: ConfigField[] = [
  {
    key: "weather.nws_contact",
    label: "NWS Contact (optional)",
    type: "text",
    placeholder: "you@example.com",
    helpText: "Default contact included in the NWS API request identifier. Defaults to the net Winlink address if unset.",
  },
  {
    key: "weather.alert_states",
    label: "Alert States (optional)",
    type: "text",
    placeholder: 'MN, WI',
    mono: true,
    helpText: 'Default comma-separated 2-letter state codes for NWS alerts (e.g. MN, WI). Leave blank to auto-detect from the event location.',
  },
];

const DELIVERY_DEFAULT_FIELDS: ConfigField[] = [
  {
    key: "delivery.backends",
    label: "Enabled Delivery Backends",
    type: "multiselect",
    options: [
      { value: "email", label: "Email" },
      { value: "groupsio", label: "Groups.io" },
      { value: "winlink", label: "Winlink" },
    ],
    helpText: "Default delivery channels for net-free events. (Groups.io API key is set above.)",
  },
  {
    key: "delivery.email.to_address",
    label: "Email Recipient",
    placeholder: "net-list@example.com",
    helpText: "Default email address for reminders and rosters.",
    visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("email"),
  },
  {
    key: "delivery.groupsio.group_name",
    label: "Groups.io Group Name",
    placeholder: "your-net",
    helpText: "Default target group name on groups.io.",
    visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("groupsio"),
  },
  {
    key: "delivery.winlink.target_address",
    label: "Winlink Delivery Address",
    placeholder: "NET@winlink.org",
    helpText: "Default Winlink address for reminders and rosters.",
    visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("winlink"),
  },
];

function WinlinkFormsSection() {
  const { addToast } = useToast();
  const [status, setStatus] = useState<FormsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);

  const loadStatus = () => {
    setLoading(true);
    getFormsStatus()
      .then(setStatus)
      .catch(() => addToast("Failed to load Winlink Forms status", "error"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadStatus();
  }, []);

  const handleFetch = async () => {
    setFetching(true);
    try {
      const result = await fetchFormsLibrary();
      setStatus((prev) => prev ? { ...prev, library_version: result.library_version, last_fetched_at: result.last_fetched_at } : prev);
      addToast(`Forms library updated to version ${result.library_version}`, "success");
    } catch {
      addToast("Failed to fetch Winlink Standard Forms library", "error");
    } finally {
      setFetching(false);
    }
  };

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
      <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
        Winlink Standard Forms
      </h2>
      {loading ? (
        <div className="flex justify-center py-4"><Spinner /></div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="text-sm text-text-secondary">
            <div>
              <span className="font-medium text-text-primary">Library version:</span>{" "}
              {status?.library_version ?? <span className="text-text-muted">Not downloaded</span>}
            </div>
            <div>
              <span className="font-medium text-text-primary">Last fetched:</span>{" "}
              {status?.last_fetched_at
                ? new Date(status.last_fetched_at).toLocaleString()
                : <span className="text-text-muted">—</span>}
            </div>
          </div>
          <div>
            <Button
              size="sm"
              variant="secondary"
              onClick={handleFetch}
              loading={fetching}
              title={status?.source_url ? `Download from ${status.source_url}` : "Fetch latest Winlink Standard Forms library"}
            >
              Fetch latest
            </Button>
            <div className="text-xs text-text-muted mt-1">
              Downloads and extracts the Winlink Standard Forms library used for rendering form check-ins.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ConfigPage() {
  const { addToast } = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const [savedValues, setSavedValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingSection, setSavingSection] = useState<string | null>(null);
  const [showPatAdvanced, setShowPatAdvanced] = useState(false);

  const loadConfig = () => {
    setLoading(true);
    setError(null);
    fetchConfig()
      .then((raw) => {
        const initialized = initializeGlobalConfig(raw);
        setValues(initialized);
        setSavedValues(initialized);
      })
      .catch(() => setError("Failed to load configuration"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleSectionSave = (sectionId: string) => async (keys: string[]) => {
    setSavingSection(sectionId);
    try {
      const payload: Record<string, string> = {};
      for (const k of keys) {
        const current = values[k] ?? "";
        const saved = savedValues[k] ?? "";
        // For secret fields (initialized as "" because loaded value was "***"),
        // only send the key if the admin typed something new.
        if (GLOBAL_SECRET_KEYS.has(k)) {
          if (current.length > 0) {
            payload[k] = current;
          }
          // If blank, omit — backend keeps the stored value.
        } else if (current !== saved) {
          payload[k] = current;
        }
      }
      if (Object.keys(payload).length > 0) {
        await setConfigBulk(payload);
        setSavedValues((prev) => ({ ...prev, ...payload }));
        addToast("Settings saved", "success");
      }
    } catch {
      addToast("Failed to save settings", "error");
    } finally {
      setSavingSection(null);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-8"><Spinner /></div>;
  }
  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-danger text-sm mb-2">{error}</p>
        <button onClick={loadConfig} className="text-accent text-sm hover:underline">Retry</button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-bold text-text-primary mb-6">Configuration</h1>

      <SettingsSection
        title="Auth"
        fields={AUTH_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("auth")}
        saving={savingSection === "auth"}
      />

      <OAuthProviderList />

      <SmtpForm />

      <WinlinkFormsSection />

      <SettingsSection
        title="Integrations"
        fields={INTEGRATIONS_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("integrations")}
        saving={savingSection === "integrations"}
      />

      <SettingsSection
        title="Delivery (global)"
        fields={DELIVERY_GLOBAL_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("delivery-global")}
        saving={savingSection === "delivery-global"}
      />

      <SettingsSection
        title="Delivery defaults"
        fields={DELIVERY_DEFAULT_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("delivery-defaults")}
        saving={savingSection === "delivery-defaults"}
      />

      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
        <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
          PAT defaults
        </h2>
        {PAT_DEFAULT_BASE_FIELDS.map((field) => {
          const value = values[field.key] ?? "";
          if (field.type === "boolean") {
            const checked = value === "true";
            return (
              <div key={field.key} className="mb-4">
                <label className="inline-flex items-center gap-2 text-sm text-text-primary">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => setValues((prev) => ({ ...prev, [field.key]: e.target.checked ? "true" : "false" }))}
                    className="accent-accent"
                  />
                  <span className="text-text-secondary">{field.label}</span>
                </label>
                <div className="text-xs text-text-muted mt-1">{field.helpText}</div>
              </div>
            );
          }
          return (
            <div key={field.key} className="mb-4">
              <label className="block text-sm text-text-secondary mb-1">{field.label}</label>
              <input
                type="text"
                value={value}
                onChange={(e) => setValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                placeholder={field.placeholder}
                className={`max-w-md w-full bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted ${field.mono ? "font-mono" : ""}`}
              />
              <div className="text-xs text-text-muted mt-1">{field.helpText}</div>
            </div>
          );
        })}
        <div className="mb-4">
          <button
            type="button"
            onClick={() => setShowPatAdvanced((v) => !v)}
            className="text-sm text-accent hover:underline"
          >
            {showPatAdvanced ? "Hide advanced" : "Show advanced (auth)"}
          </button>
        </div>
        {showPatAdvanced && (
          <SettingsSection
            title="PAT Advanced"
            fields={PAT_DEFAULT_ADVANCED_FIELDS}
            values={values}
            savedValues={savedValues}
            onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("pat-defaults-adv")}
            saving={savingSection === "pat-defaults-adv"}
          />
        )}
        <div className="flex justify-end mt-2">
          <Button
            size="sm"
            variant={
              PAT_DEFAULT_BASE_FIELDS.some(
                (f) => (values[f.key] ?? "") !== (savedValues[f.key] ?? ""),
              )
                ? "primary"
                : "secondary"
            }
            disabled={
              !PAT_DEFAULT_BASE_FIELDS.some(
                (f) => (values[f.key] ?? "") !== (savedValues[f.key] ?? ""),
              )
            }
            loading={savingSection === "pat-defaults"}
            onClick={() =>
              void handleSectionSave("pat-defaults")(PAT_DEFAULT_BASE_FIELDS.map((f) => f.key))
            }
          >
            Save
          </Button>
        </div>
      </div>

      <SettingsSection
        title="APRS defaults"
        fields={APRS_DEFAULT_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("aprs-defaults")}
        saving={savingSection === "aprs-defaults"}
      />

      <SettingsSection
        title="Weather defaults"
        fields={WEATHER_DEFAULT_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("weather-defaults")}
        saving={savingSection === "weather-defaults"}
      />

      <SettingsSection
        title="Callbook"
        fields={CALLBOOK_FIELDS}
        values={values}
        savedValues={savedValues}
        onChange={(k, v) => setValues((prev) => ({ ...prev, [k]: v }))}
        onSave={handleSectionSave("callbook")}
        saving={savingSection === "callbook"}
      />
    </div>
  );
}
