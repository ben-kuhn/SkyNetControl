import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { SettingsSection } from "../../components/SettingsSection";
import type { ConfigField } from "../../components/SettingsSection";
import { Spinner } from "../../components/Spinner";
import { useToast } from "../../context/ToastContext";
import { useEvent } from "../../context/EventProvider";
import { useAuth } from "../../hooks/useAuth";
import { fetchEventConfig, saveEventConfig } from "../../api/eventConfig";
import {
  updateEvent,
  addOperator,
  removeOperator,
  setEventVisibility,
  rotatePublicToken,
  transferEvent,
  deleteEvent,
} from "../../api/events";

// --- Config field definitions (mirrors NetSettingsPage pattern) ---

const EVENT_NET_FIELDS: ConfigField[] = [
  {
    key: "net_address",
    label: "Net Winlink Address",
    placeholder: "yournet@winlink.org",
    helpText: "Winlink address used for check-in message parsing and as {{ net_address }} in templates.",
  },
];

const EVENT_APRS_FIELDS: ConfigField[] = [
  {
    key: "aprs.enabled",
    label: "APRS-IS",
    type: "boolean",
    helpText: "Connect to APRS-IS during active events to show live positions and beacon event posts.",
  },
  {
    key: "aprs.callsign",
    label: "APRS Callsign",
    placeholder: "W0NE",
    mono: true,
    helpText: "Callsign used to log in and transmit objects — must be a callsign you are licensed to use.",
    visibleWhen: (v) => v["aprs.enabled"] === "true",
  },
  {
    key: "aprs.server",
    label: "APRS-IS Server",
    placeholder: "rotate.aprs2.net",
    mono: true,
    helpText: "Leave default unless you run your own server.",
    visibleWhen: (v) => v["aprs.enabled"] === "true",
  },
  {
    key: "aprs.port",
    label: "APRS-IS Port",
    placeholder: "14580",
    helpText: "Filtered-feed port.",
    visibleWhen: (v) => v["aprs.enabled"] === "true",
  },
];

const EVENT_WEATHER_FIELDS: ConfigField[] = [
  {
    key: "weather.enabled",
    label: "Weather overlay",
    type: "boolean",
    helpText: "Show a radar loop + NWS warning polygons on the live event map.",
  },
  {
    key: "weather.alert_states",
    label: "Alert states (optional)",
    type: "text",
    placeholder: '["MN","WI"]',
    mono: true,
    helpText: "JSON list of 2-letter state codes for NWS alerts. Leave blank to auto-detect from the event location.",
    visibleWhen: (v) => v["weather.enabled"] === "true",
  },
  {
    key: "weather.nws_contact",
    label: "NWS contact (optional)",
    type: "text",
    placeholder: "you@example.com",
    helpText: "Contact included in the NWS API request identifier. Defaults to the net Winlink address.",
    visibleWhen: (v) => v["weather.enabled"] === "true",
  },
];

// PAT fields: base_url always visible; auth fields behind Advanced toggle
const PAT_BASE_FIELDS: ConfigField[] = [
  {
    key: "pat_http_base_url",
    label: "PAT Base URL",
    placeholder: "http://shack:8080",
    mono: true,
    helpText: "Base URL of your PAT HTTP API (e.g. http://localhost:8080).",
  },
  {
    key: "pat_mailbox_path",
    label: "PAT Mailbox Path",
    placeholder: "~/.local/share/pat/mailbox/YOURCALL",
    helpText: "Local filesystem path to the PAT Winlink client mailbox directory.",
    mono: true,
  },
];

const PAT_ADVANCED_FIELDS: ConfigField[] = [
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
];

// Keys that the backend masks as "***" — must never be written back as "***"
const SECRET_KEYS = new Set(["pat_http_password", "pat_http_token"]);

/** Strip "***" placeholder and any key whose value equals "***" from the payload. */
function sanitizeForSave(
  config: Record<string, string>,
  savedConfig: Record<string, string>,
  keys: string[],
): Record<string, string> {
  const payload: Record<string, string> = {};
  for (const k of keys) {
    const current = config[k] ?? "";
    const saved = savedConfig[k] ?? "";
    // For secret fields initialized empty (because loaded value was "***"),
    // only send the key if the operator typed something new.
    if (SECRET_KEYS.has(k)) {
      if (current.length > 0) {
        payload[k] = current;
      }
      // If blank, omit — backend keeps the stored value.
    } else if (current !== saved) {
      payload[k] = current;
    }
  }
  return payload;
}

/** When a secret field loads as "***", initialize it as "" so the UI shows the placeholder. */
function initializeConfig(raw: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = { ...raw };
  for (const k of SECRET_KEYS) {
    if (out[k] === "***") {
      out[k] = "";
    }
  }
  return out;
}

export function EventSettingsPage() {
  const { event: snapshot, reload } = useEvent();
  const { user } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  const event = snapshot.event;
  const eventId = event.id;
  const isOwner =
    user != null &&
    (event.owner === user.callsign || event.created_by === user.callsign || user.is_admin);

  // --- General section state ---
  const [name, setName] = useState(event.name);
  const [description, setDescription] = useState(event.description ?? "");
  const [scheduledStart, setScheduledStart] = useState(event.scheduled_start ?? "");
  const [savingGeneral, setSavingGeneral] = useState(false);

  const generalDirty =
    name !== event.name ||
    description !== (event.description ?? "") ||
    scheduledStart !== (event.scheduled_start ?? "");

  useEffect(() => {
    setName(event.name);
    setDescription(event.description ?? "");
    setScheduledStart(event.scheduled_start ?? "");
  }, [event]);

  const handleSaveGeneral = async () => {
    setSavingGeneral(true);
    try {
      const patch: { name?: string; description?: string | null; scheduled_start?: string | null } = {};
      if (name !== event.name) patch.name = name;
      if (description !== (event.description ?? "")) patch.description = description || null;
      if (scheduledStart !== (event.scheduled_start ?? ""))
        patch.scheduled_start = scheduledStart || null;
      if (Object.keys(patch).length > 0) {
        await updateEvent(eventId, patch);
        reload();
      }
      addToast("Settings saved", "success");
    } catch (e) {
      addToast(`Save failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setSavingGeneral(false);
    }
  };

  // --- Visibility section state ---
  const [visibility, setVisibility] = useState(event.visibility ?? "private");
  const [savingVisibility, setSavingVisibility] = useState(false);
  const [rotatingToken, setRotatingToken] = useState(false);

  useEffect(() => {
    setVisibility(event.visibility ?? "private");
  }, [event.visibility]);

  const handleSetVisibility = async (v: string) => {
    setVisibility(v);
    setSavingVisibility(true);
    try {
      await setEventVisibility(eventId, v);
      reload();
      addToast("Visibility updated", "success");
    } catch (e) {
      addToast(`Failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setSavingVisibility(false);
    }
  };

  const handleRotateToken = async () => {
    if (!confirm("Rotate the public link? The old link will stop working immediately.")) return;
    setRotatingToken(true);
    try {
      await rotatePublicToken(eventId);
      reload();
      addToast("Public link rotated", "success");
    } catch (e) {
      addToast(`Failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setRotatingToken(false);
    }
  };

  const publicUrl =
    event.public_token ? `${window.location.origin}/e/${event.public_token}` : "";

  // --- Config section state ---
  const [config, setConfig] = useState<Record<string, string>>({});
  const [savedConfig, setSavedConfig] = useState<Record<string, string>>({});
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [savingSection, setSavingSection] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    setLoadingConfig(true);
    fetchEventConfig(eventId)
      .then((raw) => {
        const initialized = initializeConfig(raw);
        setConfig(initialized);
        setSavedConfig(initialized);
      })
      .catch(() => addToast("Failed to load event config", "error"))
      .finally(() => setLoadingConfig(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventId]);

  const handleSectionSave = (sectionId: string) => async (visibleKeys: string[]) => {
    setSavingSection(sectionId);
    try {
      const payload = sanitizeForSave(config, savedConfig, visibleKeys);
      if (Object.keys(payload).length > 0) {
        await saveEventConfig(eventId, payload);
        setSavedConfig((prev) => ({ ...prev, ...payload }));
      }
      addToast("Settings saved", "success");
    } catch {
      addToast("Failed to save settings", "error");
    } finally {
      setSavingSection(null);
    }
  };

  // --- Co-operators section state ---
  const [addCallsign, setAddCallsign] = useState("");
  const [addingOp, setAddingOp] = useState(false);
  const [removingOp, setRemovingOp] = useState<string | null>(null);

  const operators = event.operators ?? [];

  const handleAddOperator = async () => {
    const cs = addCallsign.trim().toUpperCase();
    if (!cs) return;
    setAddingOp(true);
    try {
      await addOperator(eventId, cs);
      setAddCallsign("");
      reload();
      addToast(`${cs} added as co-operator`, "success");
    } catch (e) {
      addToast(`Failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setAddingOp(false);
    }
  };

  const handleRemoveOperator = async (callsign: string) => {
    setRemovingOp(callsign);
    try {
      await removeOperator(eventId, callsign);
      reload();
      addToast(`${callsign} removed`, "success");
    } catch (e) {
      addToast(`Failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setRemovingOp(null);
    }
  };

  // --- Danger zone state ---
  const [transferCallsign, setTransferCallsign] = useState("");
  const [transferring, setTransferring] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleTransfer = async () => {
    const cs = transferCallsign.trim().toUpperCase();
    if (!cs) return;
    if (!confirm(`Transfer ownership of "${event.name}" to ${cs}? You will lose owner access.`)) return;
    setTransferring(true);
    try {
      await transferEvent(eventId, cs);
      setTransferCallsign("");
      reload();
      addToast(`Event transferred to ${cs}`, "success");
    } catch (e) {
      addToast(`Transfer failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setTransferring(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete "${event.name}"? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await deleteEvent(eventId);
      navigate("/events");
    } catch (e) {
      addToast(`Delete failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
      setDeleting(false);
    }
  };

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-bold text-text-primary mb-6">
        Event Settings: {event.name}
      </h1>

      {/* General */}
      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
        <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
          General
        </h2>
        <div className="flex flex-col gap-4">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Optional event description"
              className="w-full bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted resize-y"
            />
          </div>
          <Input
            label="Scheduled start (ISO datetime)"
            value={scheduledStart}
            onChange={(e) => setScheduledStart(e.target.value)}
            placeholder="2025-06-01T14:00:00"
            mono
          />
          <div>
            <Button
              variant={generalDirty ? "primary" : "secondary"}
              onClick={() => void handleSaveGeneral()}
              loading={savingGeneral}
              disabled={!generalDirty}
            >
              Save
            </Button>
          </div>
        </div>
      </div>

      {/* Visibility & public link (owner only) */}
      {isOwner && (
        <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
          <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
            Visibility &amp; Public Link
          </h2>
          <div className="flex flex-col gap-4">
            <div className="flex gap-4">
              {(["private", "public"] as const).map((v) => (
                <label key={v} className="inline-flex items-center gap-2 text-sm text-text-primary">
                  <input
                    type="radio"
                    name="visibility"
                    value={v}
                    checked={visibility === v}
                    onChange={() => void handleSetVisibility(v)}
                    disabled={savingVisibility}
                    className="accent-accent"
                  />
                  <span className="text-text-secondary capitalize">{v}</span>
                </label>
              ))}
            </div>
            <p className="text-xs text-text-muted">
              Public events appear on the public listing and can be viewed via a share link without logging in.
            </p>
            {visibility === "public" && publicUrl && (
              <div className="flex flex-col gap-2">
                <label className="block text-sm font-medium text-text-secondary">Share link</label>
                <div className="flex gap-2 items-center">
                  <code className="flex-1 bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary font-mono truncate">
                    {publicUrl}
                  </code>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void navigator.clipboard.writeText(publicUrl).then(() => addToast("Copied", "success"))}
                  >
                    Copy
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void handleRotateToken()}
                    loading={rotatingToken}
                  >
                    Rotate link
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Config */}
      {loadingConfig ? (
        <div className="flex justify-center py-4"><Spinner /></div>
      ) : (
        <>
          <SettingsSection
            title="Net / Winlink"
            fields={EVENT_NET_FIELDS}
            values={config}
            savedValues={savedConfig}
            onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("net")}
            saving={savingSection === "net"}
          />

          <SettingsSection
            title="APRS"
            fields={EVENT_APRS_FIELDS}
            values={config}
            savedValues={savedConfig}
            onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("aprs")}
            saving={savingSection === "aprs"}
          />

          <SettingsSection
            title="Weather overlay"
            fields={EVENT_WEATHER_FIELDS}
            values={config}
            savedValues={savedConfig}
            onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
            onSave={handleSectionSave("weather")}
            saving={savingSection === "weather"}
          />

          <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
            <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
              PAT Transport
            </h2>
            {PAT_BASE_FIELDS.map((field) => {
              const value = config[field.key] ?? "";
              return (
                <div key={field.key} className="mb-4">
                  <label className="block text-sm text-text-secondary mb-1">{field.label}</label>
                  <input
                    type="text"
                    value={value}
                    onChange={(e) => setConfig((prev) => ({ ...prev, [field.key]: e.target.value }))}
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
                onClick={() => setShowAdvanced((v) => !v)}
                className="text-sm text-accent hover:underline"
              >
                {showAdvanced ? "Hide advanced" : "Show advanced (auth)"}
              </button>
            </div>
            {showAdvanced && (
              <SettingsSection
                title="PAT Advanced"
                fields={PAT_ADVANCED_FIELDS}
                values={config}
                savedValues={savedConfig}
                onChange={(k, v) => setConfig((prev) => ({ ...prev, [k]: v }))}
                onSave={handleSectionSave("pat-adv")}
                saving={savingSection === "pat-adv"}
              />
            )}
            <div className="flex justify-end mt-2">
              <Button
                size="sm"
                variant={
                  PAT_BASE_FIELDS.some(
                    (f) => (config[f.key] ?? "") !== (savedConfig[f.key] ?? ""),
                  )
                    ? "primary"
                    : "secondary"
                }
                disabled={
                  !PAT_BASE_FIELDS.some(
                    (f) => (config[f.key] ?? "") !== (savedConfig[f.key] ?? ""),
                  )
                }
                loading={savingSection === "pat"}
                onClick={() =>
                  void handleSectionSave("pat")(PAT_BASE_FIELDS.map((f) => f.key))
                }
              >
                Save
              </Button>
            </div>
          </div>
        </>
      )}

      {/* Co-operators (owner only) */}
      {isOwner && (
        <div className="bg-bg-surface border border-border rounded-lg p-6 mb-4">
          <h2 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-4">
            Co-operators
          </h2>
          <div className="flex flex-col gap-2 mb-4">
            {operators.length === 0 ? (
              <p className="text-sm text-text-muted">No co-operators.</p>
            ) : (
              operators.map((op) => (
                <div key={op} className="flex items-center justify-between gap-2 py-1">
                  <span className="font-mono text-sm text-text-primary">{op}</span>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => void handleRemoveOperator(op)}
                    loading={removingOp === op}
                  >
                    Remove
                  </Button>
                </div>
              ))
            )}
          </div>
          <div className="flex gap-2">
            <Input
              placeholder="Callsign"
              value={addCallsign}
              onChange={(e) => setAddCallsign(e.target.value.toUpperCase())}
              mono
              className="max-w-xs"
              onKeyDown={(e) => { if (e.key === "Enter") void handleAddOperator(); }}
            />
            <Button
              size="sm"
              variant="primary"
              onClick={() => void handleAddOperator()}
              loading={addingOp}
              disabled={!addCallsign.trim()}
            >
              Add
            </Button>
          </div>
        </div>
      )}

      {/* Danger zone (owner only) */}
      {isOwner && (
        <div className="bg-bg-surface border border-danger/30 rounded-lg p-6 mb-4">
          <h2 className="text-xs font-medium text-danger uppercase tracking-wider mb-4">
            Danger Zone
          </h2>
          <div className="flex flex-col gap-6">
            <div>
              <h3 className="text-sm font-medium text-text-primary mb-2">Transfer ownership</h3>
              <p className="text-xs text-text-muted mb-3">
                Assign a new owner. You will lose owner-only access unless the new owner adds you back as a co-operator.
              </p>
              <div className="flex gap-2">
                <Input
                  placeholder="New owner callsign"
                  value={transferCallsign}
                  onChange={(e) => setTransferCallsign(e.target.value.toUpperCase())}
                  mono
                  className="max-w-xs"
                />
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => void handleTransfer()}
                  loading={transferring}
                  disabled={!transferCallsign.trim()}
                >
                  Transfer
                </Button>
              </div>
            </div>
            <div>
              <h3 className="text-sm font-medium text-text-primary mb-2">Delete event</h3>
              <p className="text-xs text-text-muted mb-3">
                Permanently delete this event and all its data. This cannot be undone.
              </p>
              <Button
                variant="danger"
                onClick={() => void handleDelete()}
                loading={deleting}
              >
                Delete event
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
