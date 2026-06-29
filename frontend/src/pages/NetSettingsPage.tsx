import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { SettingsSection } from "../components/SettingsSection";
import type { ConfigField } from "../components/SettingsSection";
import { Spinner } from "../components/Spinner";
import { useToast } from "../context/ToastContext";
import { useAuth } from "../hooks/useAuth";
import { useCurrentNet } from "../hooks/useCurrentNet";
import { getNetConfig, patchNet, sendGroupsIoTest, setNetConfigBulk } from "../api/nets";

function parseStringArray(raw: string): string[] {
  try {
    const v = JSON.parse(raw || "[]");
    return Array.isArray(v) ? v.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

function netOpsFields(winlinkEnabledSaved: boolean): ConfigField[] {
  const fields: ConfigField[] = [
    {
      key: "default_net_control",
      label: "Net Callsign",
      placeholder: "WAØXYZ",
      helpText: "Your net's club callsign — used as {{ net_callsign }} in templates.",
      mono: true,
    },
  ];
  if (winlinkEnabledSaved) {
    fields.push({
      key: "net_address",
      label: "Net Winlink Address",
      placeholder: "yournet@winlink.org",
      helpText:
        "Winlink address used for check-in message parsing and as {{ net_address }} in templates.",
    });
  }
  return fields;
}

const PAT_FIELDS: ConfigField[] = [
  {
    key: "pat_mailbox_path",
    label: "PAT Mailbox Path",
    placeholder: "~/.local/share/pat/mailbox/YOURCALL",
    helpText: "Local filesystem path to the PAT Winlink client mailbox directory.",
    mono: true,
  },
  {
    key: "scanner.enabled",
    label: "Auto-Scanner",
    type: "boolean",
    helpText: "Automatically scan the PAT mailbox for new check-ins on a timer.",
  },
  {
    key: "scanner.interval_minutes",
    label: "Scan Interval (minutes)",
    placeholder: "5",
    helpText: "How often to scan the mailbox for new check-ins.",
    visibleWhen: (v) => v["scanner.enabled"] === "true",
  },
];

function deliveryFields(winlinkEnabled: boolean): ConfigField[] {
  const backendOptions = [
    { value: "email", label: "Email" },
    { value: "groupsio", label: "Groups.io" },
  ];
  if (winlinkEnabled) {
    backendOptions.push({ value: "winlink", label: "Winlink" });
  }
  return [
    {
      key: "delivery.backends",
      label: "Enabled Delivery Backends",
      type: "multiselect",
      options: backendOptions,
      helpText: "Channels for sending reminders and rosters from this net.",
    },
    {
      key: "delivery.email.to_address",
      label: "Email Recipient",
      placeholder: "net-list@example.com",
      helpText: "Email address this net sends reminders and rosters to.",
      visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("email"),
    },
    {
      key: "delivery.groupsio.group_name",
      label: "Groups.io Group Name",
      placeholder: "your-net",
      helpText: "Target group name on groups.io for this net.",
      visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("groupsio"),
    },
    {
      key: "delivery.winlink.target_address",
      label: "Winlink Delivery Address",
      placeholder: "NET@winlink.org",
      helpText: "Winlink address this net sends reminders and rosters to.",
      visibleWhen: (v) => parseStringArray(v["delivery.backends"] ?? "").includes("winlink"),
    },
  ];
}

export function NetSettingsPage() {
  const { net, slug } = useCurrentNet();
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [slugDraft, setSlugDraft] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [winlinkEnabled, setWinlinkEnabled] = useState(true);
  const [savingMeta, setSavingMeta] = useState(false);

  const [config, setConfig] = useState<Record<string, string>>({});
  const [savedConfig, setSavedConfig] = useState<Record<string, string>>({});
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [savingSection, setSavingSection] = useState<string | null>(null);

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
        setWinlinkEnabled((c["winlink_enabled"] ?? "true") === "true");
      })
      .catch(() => addToast("Failed to load per-net config", "error"))
      .finally(() => setLoadingConfig(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  if (!net) {
    return <div className="flex justify-center py-8"><Spinner /></div>;
  }

  const winlinkEnabledSaved = (savedConfig["winlink_enabled"] ?? "true") === "true";
  const generalDirty =
    name !== net.name ||
    slugDraft !== net.slug ||
    isPublic !== net.is_public ||
    winlinkEnabled !== winlinkEnabledSaved;

  const handleSaveGeneral = async () => {
    setSavingMeta(true);
    try {
      const patch: { name?: string; slug?: string; is_public?: boolean } = {};
      if (name !== net.name) patch.name = name;
      if (isAdmin && slugDraft !== net.slug) patch.slug = slugDraft;
      if (isAdmin && isPublic !== net.is_public) patch.is_public = isPublic;
      const newSlug =
        Object.keys(patch).length > 0
          ? (await patchNet(net.slug, patch)).slug
          : net.slug;
      if (winlinkEnabled !== winlinkEnabledSaved) {
        const wlValue = winlinkEnabled ? "true" : "false";
        await setNetConfigBulk(newSlug, { winlink_enabled: wlValue });
        setSavedConfig((prev) => ({ ...prev, winlink_enabled: wlValue }));
        setConfig((prev) => ({ ...prev, winlink_enabled: wlValue }));
      }
      addToast("Settings saved", "success");
      await refreshUser();
      if (patch.slug && patch.slug !== net.slug) {
        navigate(`/nets/${newSlug}/settings`, { replace: true });
      }
    } catch (e) {
      addToast(`Save failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setSavingMeta(false);
    }
  };

  const handleSectionSave = (sectionId: string) => async (keys: string[]) => {
    setSavingSection(sectionId);
    try {
      const payload: Record<string, string> = {};
      for (const k of keys) {
        if ((config[k] ?? "") !== (savedConfig[k] ?? "")) {
          payload[k] = config[k] ?? "";
        }
      }
      await setNetConfigBulk(slug, payload);
      setSavedConfig((prev) => ({ ...prev, ...payload }));
      addToast("Settings saved", "success");
    } catch {
      addToast("Failed to save settings", "error");
    } finally {
      setSavingSection(null);
    }
  };

  const handleGroupsIoTest = async () => {
    if (!confirm("Post a test message to this net's configured groups.io group?")) return;
    try {
      const result = await sendGroupsIoTest(slug ?? "");
      if (result.ok) addToast("Test message posted to groups.io.", "success");
      else addToast(`Groups.io test failed: ${result.error ?? "unknown error"}`, "error");
    } catch (e: any) {
      addToast(`Groups.io test failed: ${e?.detail ?? e?.message ?? "request error"}`, "error");
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
              <p className="text-xs text-text-muted mt-1">Slug changes require admin.</p>
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
          <label className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={winlinkEnabled}
              onChange={(e) => setWinlinkEnabled(e.target.checked)}
              className="accent-accent"
            />
            <span className="text-text-secondary">
              Winlink-enabled (shows Winlink Address, PAT, and Winlink delivery options)
            </span>
          </label>
          <div>
            <Button
              variant={generalDirty ? "primary" : "secondary"}
              onClick={handleSaveGeneral}
              loading={savingMeta}
              disabled={!generalDirty}
            >
              Save
            </Button>
          </div>
        </div>
      </div>

      {loadingConfig ? (
        <div className="flex justify-center py-4"><Spinner /></div>
      ) : (
        <>
          <SettingsSection
            title="Net Operations"
            fields={netOpsFields(winlinkEnabledSaved)}
            values={config}
            savedValues={savedConfig}
            onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("net-ops")}
            saving={savingSection === "net-ops"}
          />

          {winlinkEnabledSaved && (
            <SettingsSection
              title="PAT"
              fields={PAT_FIELDS}
              values={config}
              savedValues={savedConfig}
              onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
              onSave={handleSectionSave("pat")}
              saving={savingSection === "pat"}
            />
          )}

          <SettingsSection
            title="Delivery"
            fields={deliveryFields(winlinkEnabledSaved)}
            values={config}
            savedValues={savedConfig}
            onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("delivery")}
            saving={savingSection === "delivery"}
          >
            {savedConfig["delivery.groupsio.group_name"] && (
              <Button size="sm" variant="secondary" onClick={handleGroupsIoTest}>
                Send groups.io test
              </Button>
            )}
          </SettingsSection>
        </>
      )}
    </div>
  );
}
