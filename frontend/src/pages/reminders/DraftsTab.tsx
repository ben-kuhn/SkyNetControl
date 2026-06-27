import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approveReminder,
  fetchReminders,
  generateReminderDraft,
  regenerateReminderDraft,
  sendReminder,
  skipReminder,
  updateReminderDraft,
} from "../../api/reminders";
import { fetchSessions } from "../../api/schedule";
import { useCurrentNet } from "../../hooks/useCurrentNet";
import type { Reminder, ReminderStatus, Session } from "../../types";
import { useToast } from "../../context/ToastContext";

const STATUSES: ReminderStatus[] = ["draft", "approved", "sent", "skipped"];
const STATUS_LABEL: Record<ReminderStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  skipped: "Skipped",
};
const PILL_CLS: Record<ReminderStatus, string> = {
  draft: "bg-warning/[0.12] text-warning",
  approved: "bg-accent/[0.12] text-accent",
  sent: "bg-success/[0.12] text-success",
  skipped: "bg-text-muted/[0.12] text-text-muted",
};

function formatShortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatLongDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function DraftsTab() {
  const { slug } = useCurrentNet();
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ReminderStatus>("draft");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showGenerateModal, setShowGenerateModal] = useState(false);

  const { addToast } = useToast();

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [rs, ss] = await Promise.all([fetchReminders(slug), fetchSessions(slug)]);
      setReminders(rs);
      setSessions(ss);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const sessionById = useMemo(() => {
    const map = new Map<number, Session>();
    for (const s of sessions) map.set(s.id, s);
    return map;
  }, [sessions]);

  const counts = useMemo(() => {
    const c: Record<ReminderStatus, number> = { draft: 0, approved: 0, sent: 0, skipped: 0 };
    for (const r of reminders) c[r.status]++;
    return c;
  }, [reminders]);

  const visible = useMemo(
    () => reminders.filter((r) => r.status === statusFilter),
    [reminders, statusFilter],
  );

  const selected = selectedId ? reminders.find((r) => r.id === selectedId) ?? null : null;

  return (
    <div>
      <div className="flex justify-end mb-2">
        <button
          onClick={() => setShowGenerateModal(true)}
          className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90"
        >
          + Generate draft
        </button>
      </div>
      <div className="flex gap-2 mb-3">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 text-xs rounded-md border flex items-center gap-2 transition-colors ${
              statusFilter === s
                ? "bg-accent/[0.08] border-accent text-text-primary font-medium"
                : "bg-bg-elevated border-border text-text-muted hover:text-text-primary"
            }`}
          >
            {STATUS_LABEL[s]}
            <span
              className={`text-[0.6875rem] px-1.5 py-0.5 rounded ${
                statusFilter === s ? "bg-accent text-bg-base" : "bg-bg-base text-text-muted"
              }`}
            >
              {counts[s]}
            </span>
          </button>
        ))}
      </div>

      {loading && <p className="text-text-muted text-sm py-4">Loading…</p>}
      {error && <p className="text-error text-sm py-4">{error}</p>}

      {!loading && !error && (
        <div className="flex flex-col lg:flex-row gap-4">
          <div className="flex-1 min-w-0">
            <div className="border border-border rounded-lg overflow-auto">
              <table className="w-full text-[0.8125rem] border-collapse">
                <thead className="bg-bg-elevated">
                  <tr>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Session</th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Subject</th>
                    <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((r) => {
                    const isSelected = r.id === selectedId;
                    const sess = sessionById.get(r.session_id);
                    return (
                      <tr
                        key={r.id}
                        onClick={() => setSelectedId(isSelected ? null : r.id)}
                        className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-accent/[0.08] border-l-2 border-l-accent"
                            : "hover:bg-bg-elevated/50"
                        }`}
                      >
                        <td className="px-3 py-2.5 text-text-secondary tabular-nums">
                          {sess ? formatShortDate(sess.start_date) : `#${r.session_id}`}
                        </td>
                        <td className="px-3 py-2.5 text-text-primary">
                          <span className="block truncate max-w-md" title={r.content_subject}>
                            {r.content_subject}
                          </span>
                        </td>
                        <td className="px-3 py-2.5">
                          <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${PILL_CLS[r.status]}`}>
                            {STATUS_LABEL[r.status]}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                  {visible.length === 0 && (
                    <tr>
                      <td colSpan={3} className="px-3 py-8 text-center text-text-muted text-sm">
                        No {STATUS_LABEL[statusFilter].toLowerCase()} reminders.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {selected && (
            <div className="flex-1 min-w-0">
              <DetailPanel
                reminder={selected}
                session={sessionById.get(selected.session_id) ?? null}
                slug={slug}
                onClose={() => setSelectedId(null)}
                onChanged={(updated) =>
                  setReminders((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
                }
                onError={(msg) => addToast(msg, "error")}
                onInfo={(msg) => addToast(msg, "success")}
              />
            </div>
          )}
        </div>
      )}
      {showGenerateModal && (
        <GenerateModal
          sessions={sessions.filter((s) => s.status === "scheduled")}
          slug={slug}
          onClose={() => setShowGenerateModal(false)}
          onGenerated={(generated) => {
            setReminders((prev) => {
              const exists = prev.some((r) => r.id === generated.id);
              return exists ? prev.map((r) => (r.id === generated.id ? generated : r)) : [generated, ...prev];
            });
            setStatusFilter("draft");
            setSelectedId(generated.id);
            setShowGenerateModal(false);
          }}
          onError={(msg) => addToast(msg, "error")}
        />
      )}
    </div>
  );
}

function DetailPanel({
  reminder,
  session,
  slug,
  onClose,
  onChanged,
  onError,
  onInfo,
}: {
  reminder: Reminder;
  session: Session | null;
  slug: string;
  onClose: () => void;
  onChanged: (r: Reminder) => void;
  onError: (msg: string) => void;
  onInfo: (msg: string) => void;
}) {
  const [subject, setSubject] = useState(reminder.content_subject);
  const [body, setBody] = useState(reminder.content_body);

  useEffect(() => {
    setSubject(reminder.content_subject);
    setBody(reminder.content_body);
  }, [reminder.id, reminder.content_subject, reminder.content_body]);

  const isDraft = reminder.status === "draft";
  const isApproved = reminder.status === "approved";

  const handleSave = async () => {
    try {
      const updated = await updateReminderDraft(reminder.id, {
        content_subject: subject,
        content_body: body,
      }, slug);
      onChanged(updated);
      onInfo("Draft saved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Save failed");
    }
  };

  const handleApprove = async () => {
    try {
      const updated = await approveReminder(reminder.id, slug);
      onChanged(updated);
      onInfo("Reminder approved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Approve failed");
    }
  };

  const handleSend = async () => {
    try {
      const updated = await sendReminder(reminder.id, slug);
      onChanged(updated);
      onInfo("Reminder sent.");
    } catch (e: any) {
      if (e?.status === 409) {
        onError("Send failed — verify delivery backends are configured (Config page).");
      } else {
        onError(e?.detail ?? e?.message ?? "Send failed");
      }
    }
  };

  const handleSkip = async () => {
    if (!confirm("Skip this reminder?")) return;
    try {
      const updated = await skipReminder(reminder.id, slug);
      onChanged(updated);
      onInfo("Reminder skipped.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Skip failed");
    }
  };

  const handleRegenerate = async () => {
    if (!confirm("Replace the current subject and body with a fresh render? Any unsaved edits will be lost.")) {
      return;
    }
    try {
      const updated = await regenerateReminderDraft(reminder.id, slug);
      onChanged(updated);
      onInfo("Reminder regenerated.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Regenerate failed");
    }
  };

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3 pb-3 border-b border-border">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            {session ? formatLongDate(session.start_date) : `Session #${reminder.session_id}`}
            <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${PILL_CLS[reminder.status]}`}>
              {STATUS_LABEL[reminder.status]}
            </span>
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            Drafted {formatLongDate(reminder.drafted_at)}
            {reminder.approved_at && ` · Approved ${formatLongDate(reminder.approved_at)} by ${reminder.approved_by}`}
            {reminder.sent_at && ` · Sent ${formatLongDate(reminder.sent_at)}`}
          </p>
        </div>
        <button
          onClick={onClose}
          className="text-text-muted hover:text-text-primary p-1 rounded"
          aria-label="Close detail panel"
        >
          <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="mb-3">
        <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Subject</label>
        <input
          type="text"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          disabled={!isDraft}
          className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary disabled:opacity-60"
        />
      </div>

      <div className="mb-3">
        <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">Body</label>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          disabled={!isDraft}
          rows={12}
          className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono disabled:opacity-60"
        />
      </div>

      <div className="flex gap-2 flex-wrap pt-3 border-t border-border">
        {isDraft && (
          <>
            <button onClick={handleSave} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
              Save draft
            </button>
            <button onClick={handleRegenerate} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
              Regenerate from template
            </button>
            <button onClick={handleApprove} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
              Approve
            </button>
          </>
        )}
        {isApproved && (
          <button onClick={handleSend} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
            Send
          </button>
        )}
        {(isDraft || isApproved) && (
          <button onClick={handleSkip} className="px-3 py-1.5 text-sm border border-warning/40 rounded-md text-warning hover:bg-warning/[0.08]">
            Skip
          </button>
        )}
      </div>
    </div>
  );
}

function GenerateModal({
  sessions,
  slug,
  onClose,
  onGenerated,
  onError,
}: {
  sessions: Session[];
  slug: string;
  onClose: () => void;
  onGenerated: (r: Reminder) => void;
  onError: (msg: string) => void;
}) {
  const [sessionId, setSessionId] = useState<number | "">("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (sessionId === "") return;
    setSubmitting(true);
    try {
      const generated = await generateReminderDraft(Number(sessionId), slug);
      onGenerated(generated);
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Generate failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-text-primary mb-3">Generate reminder draft</h3>
        <label className="block text-xs uppercase tracking-wider text-text-muted font-semibold mb-1">Session</label>
        <select
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value === "" ? "" : Number(e.target.value))}
          className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary mb-4"
        >
          <option value="">Select a scheduled session…</option>
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {new Date(s.start_date).toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
              })}
            </option>
          ))}
        </select>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={sessionId === "" || submitting}
            className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Generating…" : "Generate"}
          </button>
        </div>
      </div>
    </div>
  );
}
