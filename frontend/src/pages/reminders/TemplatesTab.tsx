import { useEffect, useState } from "react";
import {
  createReminderTemplate,
  deleteReminderTemplate,
  fetchReminderTemplateDefaults,
  fetchReminderTemplates,
  updateReminderTemplate,
  type ReminderTemplateDefault,
  type TemplateInput,
} from "../../api/reminders";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import type { ReminderTemplate, ReminderTemplateType } from "../../types";
import { useToast } from "../../context/ToastContext";

const TYPE_LABEL: Record<ReminderTemplateType, string> = {
  regular_checkin: "Regular check-in",
  activity: "Activity",
};

// Modal modes:
// - edit: existing row, PATCH on save
// - create-blank: shipped-defaults fetch failed; user starts empty (still hits POST)
// - create-from-default: pre-filled from the shipped seed for the chosen type
// - create-from-clone: pre-filled from an existing row (the row's name is suffixed " (copy)")
type ModalState =
  | { kind: "closed" }
  | { kind: "edit"; template: ReminderTemplate }
  | { kind: "create"; seedByType: Record<ReminderTemplateType, ReminderTemplateDefault | null>; clonedFrom: ReminderTemplate | null };

export function TemplatesTab() {
  const { slug } = useCurrentNet();
  const [templates, setTemplates] = useState<ReminderTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>({ kind: "closed" });

  const { addToast } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchReminderTemplates(slug);
      setTemplates(data);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load templates");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const handleDelete = async (t: ReminderTemplate) => {
    if (t.is_default) return;
    if (!confirm(`Delete template "${t.name}"?`)) return;
    try {
      await deleteReminderTemplate(t.id, slug);
      addToast("Template deleted.", "success");
      load();
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Delete failed", "error");
    }
  };

  const openCreate = async () => {
    // Fetch shipped defaults so the modal can pre-fill from the matching
    // type. Falls back to blank if the fetch fails (defaults are a nicety,
    // not a hard requirement).
    let seedByType: Record<ReminderTemplateType, ReminderTemplateDefault | null> = {
      regular_checkin: null,
      activity: null,
    };
    try {
      const defaults = await fetchReminderTemplateDefaults(slug);
      for (const d of defaults) {
        seedByType[d.template_type] = d;
      }
    } catch {
      // ignore — user gets a blank form
    }
    setModal({ kind: "create", seedByType, clonedFrom: null });
  };

  const openClone = (t: ReminderTemplate) => {
    setModal({
      kind: "create",
      seedByType: { regular_checkin: null, activity: null },
      clonedFrom: t,
    });
  };

  return (
    <div>
      <div className="flex justify-end mb-3">
        <button
          onClick={openCreate}
          className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90"
        >
          + New template
        </button>
      </div>

      {loading && <p className="text-text-muted text-sm py-4">Loading…</p>}
      {error && <p className="text-error text-sm py-4">{error}</p>}

      {!loading && !error && (
        <div className="border border-border rounded-lg overflow-auto">
          <table className="w-full text-[0.8125rem] border-collapse">
            <thead className="bg-bg-elevated">
              <tr>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Name</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Type</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Lead time</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Default</th>
                <th className="border-b border-border w-40"></th>
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => (
                <tr key={t.id} className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50">
                  <td className="px-3 py-2.5 text-text-primary">{t.name}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{TYPE_LABEL[t.template_type]}</td>
                  <td className="px-3 py-2.5 text-text-secondary">{t.lead_time_days} day{t.lead_time_days === 1 ? "" : "s"}</td>
                  <td className="px-3 py-2.5">
                    {t.is_default && (
                      <span className="inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium bg-accent/[0.12] text-accent">
                        default
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <button
                      onClick={() => setModal({ kind: "edit", template: t })}
                      className="text-text-muted hover:text-accent text-xs mr-2"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => openClone(t)}
                      className="text-text-muted hover:text-accent text-xs mr-2"
                    >
                      Clone
                    </button>
                    <button
                      onClick={() => handleDelete(t)}
                      disabled={t.is_default}
                      className="text-text-muted hover:text-error text-xs disabled:opacity-40 disabled:hover:text-text-muted"
                      title={t.is_default ? "Cannot delete default template" : "Delete"}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {templates.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-text-muted text-sm">
                    No templates yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {modal.kind !== "closed" && (
        <TemplateModal
          modal={modal}
          slug={slug}
          onClose={() => setModal({ kind: "closed" })}
          onSaved={() => {
            setModal({ kind: "closed" });
            load();
          }}
          onError={(msg) => addToast(msg, "error")}
          onInfo={(msg) => addToast(msg, "success")}
        />
      )}
    </div>
  );
}

function initialFormFromModal(modal: Exclude<ModalState, { kind: "closed" }>) {
  if (modal.kind === "edit") {
    const t = modal.template;
    return {
      name: t.name,
      type: t.template_type,
      subject: t.subject_template,
      body: t.body_template,
      leadTime: t.lead_time_days,
      isDefault: t.is_default,
    };
  }
  // create
  if (modal.clonedFrom) {
    const t = modal.clonedFrom;
    return {
      name: `${t.name} (copy)`,
      type: t.template_type,
      subject: t.subject_template,
      body: t.body_template,
      leadTime: t.lead_time_days,
      isDefault: false, // a clone never inherits default flag — would collide
    };
  }
  const seed = modal.seedByType.regular_checkin;
  return {
    name: "",
    type: "regular_checkin" as ReminderTemplateType,
    subject: seed?.subject_template ?? "",
    body: seed?.body_template ?? "",
    leadTime: seed?.lead_time_days ?? 3,
    isDefault: false,
  };
}

function TemplateModal({
  modal,
  slug,
  onClose,
  onSaved,
  onError,
  onInfo,
}: {
  modal: Exclude<ModalState, { kind: "closed" }>;
  slug: string;
  onClose: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
  onInfo: (msg: string) => void;
}) {
  const initial = initialFormFromModal(modal);
  const [name, setName] = useState(initial.name);
  const [type, setType] = useState<ReminderTemplateType>(initial.type);
  const [subject, setSubject] = useState(initial.subject);
  const [body, setBody] = useState(initial.body);
  const [leadTime, setLeadTime] = useState(initial.leadTime);
  const [isDefault, setIsDefault] = useState(initial.isDefault);
  const [submitting, setSubmitting] = useState(false);

  // Track whether the body/subject still match the seed for the currently
  // selected type. If they do, swapping type swaps in the new seed too;
  // if the user has edited away from the seed, we preserve their edits.
  const isCreateFromDefault =
    modal.kind === "create" && modal.clonedFrom === null;

  const handleTypeChange = (newType: ReminderTemplateType) => {
    if (isCreateFromDefault) {
      const oldSeed = modal.seedByType[type];
      const userEditedSubject = oldSeed ? subject !== oldSeed.subject_template : subject !== "";
      const userEditedBody = oldSeed ? body !== oldSeed.body_template : body !== "";
      const newSeed = modal.seedByType[newType];
      if (!userEditedSubject) setSubject(newSeed?.subject_template ?? "");
      if (!userEditedBody) setBody(newSeed?.body_template ?? "");
      if (!userEditedSubject && !userEditedBody && newSeed) {
        setLeadTime(newSeed.lead_time_days);
      }
    }
    setType(newType);
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    const input: TemplateInput = {
      name,
      template_type: type,
      subject_template: subject,
      body_template: body,
      lead_time_days: leadTime,
      is_default: isDefault,
    };
    try {
      if (modal.kind === "edit") {
        await updateReminderTemplate(modal.template.id, input, slug);
        onInfo("Template updated.");
      } else {
        await createReminderTemplate(input, slug);
        onInfo("Template created.");
      }
      onSaved();
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSubmitting(false);
    }
  };

  const title =
    modal.kind === "edit"
      ? "Edit template"
      : modal.clonedFrom
        ? `Clone of ${modal.clonedFrom.name}`
        : "New template";

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-2xl max-h-[90vh] overflow-auto"
      >
        <h3 className="text-lg font-semibold text-text-primary mb-3">{title}</h3>

        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div>
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Type</label>
            <select
              value={type}
              onChange={(e) => handleTypeChange(e.target.value as ReminderTemplateType)}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            >
              <option value="regular_checkin">Regular check-in</option>
              <option value="activity">Activity</option>
            </select>
          </div>
        </div>

        <div className="mb-3">
          <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Subject template</label>
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
          />
        </div>

        <div className="mb-3">
          <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Body template</label>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={10}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
          <p className="text-xs text-text-muted mt-1">
            Placeholders: <code>{"{{ date }}"}</code>, <code>{"{{ time }}"}</code>, <code>{"{{ day_of_week }}"}</code>, <code>{"{{ activity_title }}"}</code>, <code>{"{{ activity_instructions }}"}</code>, <code>{"{{ net_control }}"}</code>, <code>{"{{ next_week_preview }}"}</code>, <code>{"{{ net_callsign }}"}</code>, <code>{"{{ net_address }}"}</code>
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Lead time (days)</label>
            <input
              type="number"
              min={0}
              value={leadTime}
              onChange={(e) => setLeadTime(Number(e.target.value))}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm text-text-primary">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
              />
              Default for this type
            </label>
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!name || !subject || !body || submitting}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
