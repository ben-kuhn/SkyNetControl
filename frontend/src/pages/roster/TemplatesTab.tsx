import { useEffect, useState } from "react";
import {
  createRosterTemplate,
  deleteRosterTemplate,
  fetchRosterTemplateDefaults,
  fetchRosterTemplates,
  updateRosterTemplate,
  type RosterTemplateDefault,
  type RosterTemplateInput,
} from "../../api/roster";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import type { RosterTemplate } from "../../types";
import { useToast } from "../../context/ToastContext";

const PLACEHOLDER_HINT =
  "Placeholders: {{ date }}, {{ time }}, {{ day_of_week }}, {{ net_control }}, {{ net_callsign }}, {{ net_address }}, {{ activity_title }}, {{ activity_instructions }}, {{ next_week_preview }}, {{ session_url }}, {{ total_count }}, {{ checkins }} (use {% for c in checkins %}…{% endfor %}), {{ new_members }}";

// Modal modes:
// - edit: existing row, PATCH on save
// - create with seed=null: blank form (default fetch failed)
// - create with seed=<default>: pre-filled from shipped default
// - create with seed=<clone>: pre-filled from an existing row, name suffixed " (copy)"
type ModalState =
  | { kind: "closed" }
  | { kind: "edit"; template: RosterTemplate }
  | { kind: "create"; seed: RosterTemplateDefault | null; clonedFrom: RosterTemplate | null };

export function TemplatesTab() {
  const { slug } = useCurrentNet();
  const [templates, setTemplates] = useState<RosterTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>({ kind: "closed" });

  const { addToast } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchRosterTemplates(slug);
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

  const handleDelete = async (t: RosterTemplate) => {
    if (t.is_default) return;
    if (!confirm(`Delete template "${t.name}"?`)) return;
    try {
      await deleteRosterTemplate(t.id, slug);
      addToast("Template deleted.", "success");
      load();
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Delete failed", "error");
    }
  };

  const openCreate = async () => {
    // Fetch shipped defaults so the modal can pre-fill. Falls back to
    // blank if the fetch fails (defaults are a nicety, not required).
    let seed: RosterTemplateDefault | null = null;
    try {
      const defaults = await fetchRosterTemplateDefaults(slug);
      seed = defaults[0] ?? null;
    } catch {
      // ignore — user gets a blank form
    }
    setModal({ kind: "create", seed, clonedFrom: null });
  };

  const openClone = (t: RosterTemplate) => {
    setModal({ kind: "create", seed: null, clonedFrom: t });
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
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Lead time</th>
                <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Default</th>
                <th className="border-b border-border w-40"></th>
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => (
                <tr key={t.id} className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50">
                  <td className="px-3 py-2.5 text-text-primary">{t.name}</td>
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
                  <td colSpan={4} className="px-3 py-8 text-center text-text-muted text-sm">
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
      subject: t.subject_template,
      header: t.header_template,
      welcome: t.welcome_template,
      comments: t.comments_template,
      footer: t.footer_template,
      leadTime: t.lead_time_days,
      isDefault: t.is_default,
    };
  }
  // create
  if (modal.clonedFrom) {
    const t = modal.clonedFrom;
    return {
      name: `${t.name} (copy)`,
      subject: t.subject_template,
      header: t.header_template,
      welcome: t.welcome_template,
      comments: t.comments_template,
      footer: t.footer_template,
      leadTime: t.lead_time_days,
      isDefault: false, // clones never inherit default — would collide
    };
  }
  const seed = modal.seed;
  return {
    name: "",
    subject: seed?.subject_template ?? "",
    header: seed?.header_template ?? "",
    welcome: seed?.welcome_template ?? "",
    comments: seed?.comments_template ?? "",
    footer: seed?.footer_template ?? "",
    leadTime: seed?.lead_time_days ?? 1,
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
  const [subject, setSubject] = useState(initial.subject);
  const [header, setHeader] = useState(initial.header);
  const [welcome, setWelcome] = useState(initial.welcome);
  const [comments, setComments] = useState(initial.comments);
  const [footer, setFooter] = useState(initial.footer);
  const [leadTime, setLeadTime] = useState(initial.leadTime);
  const [isDefault, setIsDefault] = useState(initial.isDefault);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    const input: RosterTemplateInput = {
      name,
      subject_template: subject,
      header_template: header,
      welcome_template: welcome,
      comments_template: comments,
      footer_template: footer,
      lead_time_days: leadTime,
      is_default: isDefault,
    };
    try {
      if (modal.kind === "edit") {
        await updateRosterTemplate(modal.template.id, input, slug);
        onInfo("Template updated.");
      } else {
        await createRosterTemplate(input, slug);
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
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-3xl max-h-[90vh] overflow-auto"
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
            <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Lead time (days)</label>
            <input
              type="number"
              min={0}
              value={leadTime}
              onChange={(e) => setLeadTime(Number(e.target.value))}
              className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
            />
          </div>
        </div>

        <Field label="Subject template">
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary"
          />
        </Field>

        <Field label="Header template">
          <textarea
            value={header}
            onChange={(e) => setHeader(e.target.value)}
            rows={4}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <Field label="Welcome template">
          <textarea
            value={welcome}
            onChange={(e) => setWelcome(e.target.value)}
            rows={4}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <Field label="Comments template">
          <textarea
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            rows={4}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <Field label="Footer template">
          <textarea
            value={footer}
            onChange={(e) => setFooter(e.target.value)}
            rows={3}
            className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono"
          />
        </Field>

        <p className="text-xs text-text-muted mb-3">{PLACEHOLDER_HINT}</p>

        <div className="mb-4">
          <label className="flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Default template
          </label>
        </div>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!name || !subject || !header || !welcome || !comments || !footer || submitting}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">{label}</label>
      {children}
    </div>
  );
}
