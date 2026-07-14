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

  const loadConfig = () => {
    setLoading(true);
    setError(null);
    fetchConfig()
      .then((config) => {
        setValues(config);
        setSavedValues(config);
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
        if ((values[k] ?? "") !== (savedValues[k] ?? "")) {
          payload[k] = values[k] ?? "";
        }
      }
      await setConfigBulk(payload);
      setSavedValues((prev) => ({ ...prev, ...payload }));
      addToast("Settings saved", "success");
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
