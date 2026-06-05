import { useEffect, useState } from "react";
import { useToast } from "../context/ToastContext";
import { fetchConfig, setConfigValue } from "../api/config";
import { Button } from "../components/Button";
import { Spinner } from "../components/Spinner";

type ConfigFieldType = "text" | "boolean" | "multiselect";

interface MultiSelectOption {
  value: string;
  label: string;
}

interface ConfigField {
  key: string;
  label: string;
  group: string;
  helpText: string;
  type?: ConfigFieldType; // defaults to "text"
  placeholder?: string;   // text fields only
  mono?: boolean;         // text fields only
  secret?: boolean;       // text fields only
  options?: MultiSelectOption[]; // multiselect only
  visibleWhen?: (values: Record<string, string>) => boolean;
}

const DELIVERY_BACKEND_OPTIONS: MultiSelectOption[] = [
  { value: "email", label: "Email" },
  { value: "groupsio", label: "Groups.io" },
  { value: "winlink", label: "Winlink" },
];

function parseBackends(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

const CONFIG_FIELDS: ConfigField[] = [
  {
    key: "default_net_control",
    label: "Net Callsign",
    group: "Net Operations",
    placeholder: "WAØXYZ",
    helpText:
      "Your net's callsign — used as the default net-control assignment for new sessions and as {{ net_callsign }} in templates",
    mono: true,
  },
  {
    key: "net_address",
    label: "Net Winlink Address",
    group: "Net Operations",
    placeholder: "yournet@winlink.org",
    helpText:
      "Winlink address used for check-in message parsing and as {{ net_address }} in templates",
  },
  {
    key: "pat_mailbox_path",
    label: "PAT Mailbox Path",
    group: "PAT",
    placeholder: "~/.local/share/pat/mailbox/YOURCALL",
    helpText: "Local filesystem path to the PAT Winlink client mailbox directory",
    mono: true,
  },
  {
    key: "scanner.enabled",
    label: "Auto-Scanner",
    group: "PAT",
    type: "boolean",
    helpText: "Automatically scan the PAT mailbox for new check-ins on a timer",
  },
  {
    key: "scanner.interval_minutes",
    label: "Scan Interval (minutes)",
    group: "PAT",
    placeholder: "5",
    helpText: "How often to scan the mailbox for new check-ins",
    visibleWhen: (v) => v["scanner.enabled"] === "true",
  },
  {
    key: "claude_api_key",
    label: "Claude API Key",
    group: "Integrations",
    placeholder: "sk-ant-...",
    helpText: "API key for Claude-powered activity brainstorming (optional)",
    secret: true,
  },
  {
    key: "delivery.backends",
    label: "Enabled Delivery Backends",
    group: "Delivery",
    type: "multiselect",
    options: DELIVERY_BACKEND_OPTIONS,
    helpText: "Channels for sending reminders and rosters",
  },
  {
    key: "delivery.email.to_address",
    label: "Email Recipient",
    group: "Delivery",
    placeholder: "net-list@example.com",
    helpText: "Email address to send reminders and rosters to",
    visibleWhen: (v) =>
      parseBackends(v["delivery.backends"] ?? "").includes("email"),
  },
  {
    key: "delivery.groupsio.api_key",
    label: "Groups.io API Key",
    group: "Delivery",
    placeholder: "your-api-key",
    helpText: "API key for posting to groups.io",
    secret: true,
    visibleWhen: (v) =>
      parseBackends(v["delivery.backends"] ?? "").includes("groupsio"),
  },
  {
    key: "delivery.groupsio.group_name",
    label: "Groups.io Group Name",
    group: "Delivery",
    placeholder: "your-net",
    helpText: "Target group name on groups.io",
    visibleWhen: (v) =>
      parseBackends(v["delivery.backends"] ?? "").includes("groupsio"),
  },
  {
    key: "delivery.winlink.target_address",
    label: "Winlink Delivery Address",
    group: "Delivery",
    placeholder: "NET@winlink.org",
    helpText: "Winlink address to send reminders and rosters to",
    visibleWhen: (v) =>
      parseBackends(v["delivery.backends"] ?? "").includes("winlink"),
  },
];

const GROUPS = ["Net Operations", "PAT", "Integrations", "Delivery"];

function ConfigFieldRow({
  field,
  value,
  savedValue,
  onChange,
  onSave,
  saving,
}: {
  field: ConfigField;
  value: string;
  savedValue: string;
  onChange: (value: string) => void;
  onSave: () => void;
  saving: boolean;
}) {
  const [showSecret, setShowSecret] = useState(false);
  const isDirty = value !== savedValue;
  const type = field.type ?? "text";

  let input: React.ReactNode;
  if (type === "boolean") {
    const checked = value === "true";
    input = (
      <label className="inline-flex items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked ? "true" : "false")}
          className="accent-accent"
        />
        <span className="text-text-secondary">{checked ? "Enabled" : "Disabled"}</span>
      </label>
    );
  } else if (type === "multiselect") {
    const selected = parseBackends(value);
    const toggle = (v: string) => {
      const next = selected.includes(v)
        ? selected.filter((s) => s !== v)
        : [...selected, v];
      onChange(JSON.stringify(next));
    };
    input = (
      <div className="flex flex-col gap-1">
        {(field.options ?? []).map((opt) => (
          <label key={opt.value} className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={selected.includes(opt.value)}
              onChange={() => toggle(opt.value)}
              className="accent-accent"
            />
            <span className="text-text-secondary">{opt.label}</span>
          </label>
        ))}
      </div>
    );
  } else {
    input = (
      <div className="relative flex-1 max-w-md">
        <input
          type={field.secret && !showSecret ? "password" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          className={`w-full bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted ${
            field.mono ? "font-mono" : ""
          } ${field.secret ? "pr-10" : ""}`}
        />
        {field.secret && (
          <button
            type="button"
            onClick={() => setShowSecret(!showSecret)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary text-xs px-1"
            title={showSecret ? "Hide" : "Show"}
          >
            {showSecret ? "Hide" : "Show"}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mb-4 last:mb-0">
      <label className="block text-sm text-text-secondary mb-1">
        {field.label}
      </label>
      <div className="flex gap-2 items-start">
        <div className="flex-1">{input}</div>
        <Button
          size="sm"
          variant={isDirty ? "primary" : "secondary"}
          onClick={onSave}
          loading={saving}
          disabled={!isDirty}
        >
          Save
        </Button>
      </div>
      <div className="text-xs text-text-muted mt-1">{field.helpText}</div>
    </div>
  );
}

export function ConfigPage() {
  const { addToast } = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const [savedValues, setSavedValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);

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

  const handleSave = async (key: string) => {
    setSavingKey(key);
    try {
      await setConfigValue(key, values[key] || "");
      setSavedValues((prev) => ({ ...prev, [key]: values[key] || "" }));
      addToast("Setting saved", "success");
    } catch {
      addToast("Failed to save setting", "error");
    } finally {
      setSavingKey(null);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-danger text-sm mb-2">{error}</p>
        <button
          onClick={loadConfig}
          className="text-accent text-sm hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-bold text-text-primary mb-6">
        Configuration
      </h1>

      {GROUPS.map((group) => {
        const visibleFields = CONFIG_FIELDS.filter(
          (f) => f.group === group && (!f.visibleWhen || f.visibleWhen(values)),
        );
        if (visibleFields.length === 0) return null;
        return (
          <div
            key={group}
            className="bg-bg-surface border border-border rounded-lg p-6 mb-4"
          >
            <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
              {group}
            </h2>
            {visibleFields.map((field) => (
              <ConfigFieldRow
                key={field.key}
                field={field}
                value={values[field.key] || ""}
                savedValue={savedValues[field.key] || ""}
                onChange={(v) =>
                  setValues((prev) => ({ ...prev, [field.key]: v }))
                }
                onSave={() => handleSave(field.key)}
                saving={savingKey === field.key}
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}
