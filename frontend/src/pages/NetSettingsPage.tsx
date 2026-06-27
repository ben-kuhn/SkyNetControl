import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Spinner } from "../components/Spinner";
import { useToast } from "../context/ToastContext";
import { useAuth } from "../hooks/useAuth";
import { useCurrentNet } from "../hooks/useCurrentNet";
import { getNetConfig, patchNet, setNetConfigValue } from "../api/nets";

interface NetConfigField {
  key: string;
  label: string;
  type?: "text" | "boolean";
  placeholder?: string;
  helpText: string;
  mono?: boolean;
  group: string;
  visibleWhen?: (values: Record<string, string>) => boolean;
}

const NET_CONFIG_FIELDS: NetConfigField[] = [
  {
    key: "default_net_control",
    label: "Net Callsign",
    group: "Net Operations",
    placeholder: "WAØXYZ",
    helpText: "Your net's club callsign — used as {{ net_callsign }} in templates.",
    mono: true,
  },
  {
    key: "net_address",
    label: "Net Winlink Address",
    group: "Net Operations",
    placeholder: "yournet@winlink.org",
    helpText:
      "Winlink address used for check-in message parsing and as {{ net_address }} in templates.",
  },
  {
    key: "pat_mailbox_path",
    label: "PAT Mailbox Path",
    group: "PAT",
    placeholder: "~/.local/share/pat/mailbox/YOURCALL",
    helpText: "Local filesystem path to the PAT Winlink client mailbox directory.",
    mono: true,
  },
  {
    key: "scanner.enabled",
    label: "Auto-Scanner",
    group: "PAT",
    type: "boolean",
    helpText: "Automatically scan the PAT mailbox for new check-ins on a timer.",
  },
  {
    key: "scanner.interval_minutes",
    label: "Scan Interval (minutes)",
    group: "PAT",
    placeholder: "5",
    helpText: "How often to scan the mailbox for new check-ins.",
    visibleWhen: (v) => v["scanner.enabled"] === "true",
  },
];

const GROUPS = ["Net Operations", "PAT"];

function ConfigFieldRow({
  field,
  value,
  savedValue,
  onChange,
  onSave,
  saving,
}: {
  field: NetConfigField;
  value: string;
  savedValue: string;
  onChange: (value: string) => void;
  onSave: () => void;
  saving: boolean;
}) {
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
  } else {
    input = (
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        className={`w-full bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted ${
          field.mono ? "font-mono" : ""
        }`}
      />
    );
  }

  return (
    <div className="mb-4 last:mb-0">
      <label className="block text-sm text-text-secondary mb-1">{field.label}</label>
      <div className="flex gap-2 items-start">
        <div className="flex-1 max-w-md">{input}</div>
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

export function NetSettingsPage() {
  const { net, slug } = useCurrentNet();
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [slugDraft, setSlugDraft] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [savingMeta, setSavingMeta] = useState(false);

  const [config, setConfig] = useState<Record<string, string>>({});
  const [savedConfig, setSavedConfig] = useState<Record<string, string>>({});
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const isAdmin = user?.is_admin === true;

  useEffect(() => {
    if (!net) return;
    setName(net.name);
    setSlugDraft(net.slug);
    setIsPublic(net.is_public);
  }, [net]);

  useEffect(() => {
    if (!slug) return;
    setLoadingConfig(true);
    getNetConfig(slug)
      .then((c) => {
        setConfig(c);
        setSavedConfig(c);
      })
      .catch(() => addToast("Failed to load per-net config", "error"))
      .finally(() => setLoadingConfig(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  if (!net) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  const metaDirty =
    name !== net.name || slugDraft !== net.slug || isPublic !== net.is_public;

  const handleSaveMeta = async () => {
    setSavingMeta(true);
    try {
      const patch: { name?: string; slug?: string; is_public?: boolean } = {};
      if (name !== net.name) patch.name = name;
      if (isAdmin && slugDraft !== net.slug) patch.slug = slugDraft;
      if (isAdmin && isPublic !== net.is_public) patch.is_public = isPublic;
      const updated = await patchNet(net.slug, patch);
      addToast("Settings saved", "success");
      // Refresh user memberships so the picker reflects the new name / slug.
      await refreshUser();
      // If slug changed, navigate to the new URL.
      if (patch.slug && patch.slug !== net.slug) {
        navigate(`/nets/${updated.slug}/settings`, { replace: true });
      }
    } catch (e) {
      addToast(`Save failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setSavingMeta(false);
    }
  };

  const handleSaveConfig = async (key: string) => {
    setSavingKey(key);
    try {
      await setNetConfigValue(slug, key, config[key] || "");
      setSavedConfig((prev) => ({ ...prev, [key]: config[key] || "" }));
      addToast("Setting saved", "success");
    } catch {
      addToast("Failed to save setting", "error");
    } finally {
      setSavingKey(null);
    }
  };

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-bold text-text-primary mb-6">
        Net Settings: {net.name}
      </h1>

      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
        <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
          General
        </h2>
        <div className="flex flex-col gap-4">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <div>
            <Input
              label="Slug"
              value={slugDraft}
              onChange={(e) => setSlugDraft(e.target.value)}
              mono
              disabled={!isAdmin}
            />
            {!isAdmin && (
              <p className="text-xs text-text-muted mt-1">
                Slug changes require admin.
              </p>
            )}
          </div>
          <label className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={isPublic}
              onChange={(e) => setIsPublic(e.target.checked)}
              disabled={!isAdmin}
              className="accent-accent"
            />
            <span className="text-text-secondary">
              Public net (anonymous read access to check-ins)
            </span>
          </label>
          {!isAdmin && (
            <p className="text-xs text-text-muted -mt-2">
              Visibility changes require admin.
            </p>
          )}
          <div>
            <Button
              variant={metaDirty ? "primary" : "secondary"}
              onClick={handleSaveMeta}
              loading={savingMeta}
              disabled={!metaDirty}
            >
              Save changes
            </Button>
          </div>
        </div>
      </div>

      {loadingConfig ? (
        <div className="flex justify-center py-4">
          <Spinner />
        </div>
      ) : (
        GROUPS.map((group) => {
          const visibleFields = NET_CONFIG_FIELDS.filter(
            (f) => f.group === group && (!f.visibleWhen || f.visibleWhen(config)),
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
                  value={config[field.key] || ""}
                  savedValue={savedConfig[field.key] || ""}
                  onChange={(v) =>
                    setConfig((prev) => ({ ...prev, [field.key]: v }))
                  }
                  onSave={() => handleSaveConfig(field.key)}
                  saving={savingKey === field.key}
                />
              ))}
            </div>
          );
        })
      )}
    </div>
  );
}
