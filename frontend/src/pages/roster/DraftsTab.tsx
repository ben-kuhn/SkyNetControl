import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approveRoster,
  fetchRosters,
  previewRoster,
  sendRoster,
  skipRoster,
  updateRosterDraft,
} from "../../api/roster";
import { fetchSessions } from "../../api/schedule";
import type { Roster, RosterStatus, Session } from "../../types";
import { useToast } from "../../context/ToastContext";

const STATUSES: RosterStatus[] = ["draft", "approved", "sent", "skipped"];
const STATUS_LABEL: Record<RosterStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  skipped: "Skipped",
};
const PILL_CLS: Record<RosterStatus, string> = {
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
  const [rosters, setRosters] = useState<Roster[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<RosterStatus>("draft");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);

  const { addToast } = useToast();

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [rs, ss] = await Promise.all([fetchRosters(), fetchSessions()]);
      setRosters(rs);
      setSessions(ss);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const sessionById = useMemo(() => {
    const map = new Map<number, Session>();
    for (const s of sessions) map.set(s.id, s);
    return map;
  }, [sessions]);

  const counts = useMemo(() => {
    const c: Record<RosterStatus, number> = { draft: 0, approved: 0, sent: 0, skipped: 0 };
    for (const r of rosters) c[r.status]++;
    return c;
  }, [rosters]);

  const visible = useMemo(
    () => rosters.filter((r) => r.status === statusFilter),
    [rosters, statusFilter],
  );

  const selected = selectedId ? rosters.find((r) => r.id === selectedId) ?? null : null;

  const handlePreview = async (id: number) => {
    try {
      const { text } = await previewRoster(id);
      setPreviewText(text);
    } catch (e: any) {
      addToast(e?.detail ?? e?.message ?? "Preview failed", "error");
    }
  };

  return (
    <div>
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
                        No {STATUS_LABEL[statusFilter].toLowerCase()} rosters.
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
                roster={selected}
                session={sessionById.get(selected.session_id) ?? null}
                onClose={() => setSelectedId(null)}
                onChanged={(updated) =>
                  setRosters((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
                }
                onPreview={() => handlePreview(selected.id)}
                onError={(msg) => addToast(msg, "error")}
                onInfo={(msg) => addToast(msg, "success")}
              />
            </div>
          )}
        </div>
      )}

      {previewText !== null && (
        <PreviewModal text={previewText} onClose={() => setPreviewText(null)} />
      )}
    </div>
  );
}

function DetailPanel({
  roster,
  session,
  onClose,
  onChanged,
  onPreview,
  onError,
  onInfo,
}: {
  roster: Roster;
  session: Session | null;
  onClose: () => void;
  onChanged: (r: Roster) => void;
  onPreview: () => void;
  onError: (msg: string) => void;
  onInfo: (msg: string) => void;
}) {
  const [subject, setSubject] = useState(roster.content_subject);
  const [header, setHeader] = useState(roster.content_header);
  const [welcome, setWelcome] = useState(roster.content_welcome);
  const [comments, setComments] = useState(roster.content_comments);
  const [footer, setFooter] = useState(roster.content_footer);

  useEffect(() => {
    setSubject(roster.content_subject);
    setHeader(roster.content_header);
    setWelcome(roster.content_welcome);
    setComments(roster.content_comments);
    setFooter(roster.content_footer);
  }, [
    roster.id,
    roster.content_subject,
    roster.content_header,
    roster.content_welcome,
    roster.content_comments,
    roster.content_footer,
  ]);

  const isDraft = roster.status === "draft";
  const isApproved = roster.status === "approved";

  const handleSave = async () => {
    try {
      const updated = await updateRosterDraft(roster.id, {
        content_subject: subject,
        content_header: header,
        content_welcome: welcome,
        content_comments: comments,
        content_footer: footer,
      });
      onChanged(updated);
      onInfo("Draft saved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Save failed");
    }
  };

  const handleApprove = async () => {
    try {
      const updated = await approveRoster(roster.id);
      onChanged(updated);
      onInfo("Roster approved.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Approve failed");
    }
  };

  const handleSend = async () => {
    try {
      const updated = await sendRoster(roster.id);
      onChanged(updated);
      onInfo("Roster sent.");
    } catch (e: any) {
      if (e?.status === 409) {
        onError("Send failed — verify delivery backends are configured (Config page).");
      } else {
        onError(e?.detail ?? e?.message ?? "Send failed");
      }
    }
  };

  const handleSkip = async () => {
    if (!confirm("Skip this roster?")) return;
    try {
      const updated = await skipRoster(roster.id);
      onChanged(updated);
      onInfo("Roster skipped.");
    } catch (e: any) {
      onError(e?.detail ?? e?.message ?? "Skip failed");
    }
  };

  return (
    <div className="border border-border rounded-lg p-4 bg-bg-surface">
      <div className="flex items-start justify-between mb-3 pb-3 border-b border-border">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            {session ? formatLongDate(session.start_date) : `Session #${roster.session_id}`}
            <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${PILL_CLS[roster.status]}`}>
              {STATUS_LABEL[roster.status]}
            </span>
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            Drafted {formatLongDate(roster.drafted_at)}
            {roster.approved_at && ` · Approved ${formatLongDate(roster.approved_at)} by ${roster.approved_by}`}
            {roster.sent_at && ` · Sent ${formatLongDate(roster.sent_at)}`}
          </p>
          {roster.session_url && (
            <p className="text-xs mt-1">
              <a href={roster.session_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                {roster.session_url}
              </a>
            </p>
          )}
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

      <SectionInput label="Subject" value={subject} onChange={setSubject} disabled={!isDraft} />
      <SectionTextarea label="Header" value={header} onChange={setHeader} disabled={!isDraft} rows={6} />
      <SectionTextarea label="Welcome" value={welcome} onChange={setWelcome} disabled={!isDraft} rows={6} />
      <SectionTextarea label="Comments" value={comments} onChange={setComments} disabled={!isDraft} rows={6} />
      <SectionTextarea label="Footer" value={footer} onChange={setFooter} disabled={!isDraft} rows={4} />

      <div className="flex gap-2 flex-wrap pt-3 border-t border-border">
        {isDraft && (
          <button onClick={handleSave} className="px-3 py-1.5 text-sm bg-accent text-bg-base rounded-md font-medium hover:opacity-90">
            Save draft
          </button>
        )}
        <button onClick={onPreview} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
          Preview
        </button>
        {isDraft && (
          <button onClick={handleApprove} className="px-3 py-1.5 text-sm border border-border rounded-md text-text-primary hover:bg-bg-elevated">
            Approve
          </button>
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

function SectionInput({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="mb-3">
      <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-bg-elevated text-text-primary disabled:opacity-60"
      />
    </div>
  );
}

function SectionTextarea({
  label,
  value,
  onChange,
  disabled,
  rows,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  rows: number;
}) {
  return (
    <div className="mb-3">
      <label className="block text-[0.6875rem] uppercase tracking-wider text-text-muted font-semibold mb-1">{label}</label>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={rows}
        className="w-full px-3 py-2 text-[0.8125rem] border border-border rounded-lg bg-bg-elevated text-text-primary font-mono disabled:opacity-60"
      />
    </div>
  );
}

function PreviewModal({ text, onClose }: { text: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-bg-surface border border-border rounded-lg p-5 w-full max-w-3xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3 pb-3 border-b border-border">
          <h3 className="text-lg font-semibold text-text-primary">Preview</h3>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary p-1 rounded"
            aria-label="Close preview"
          >
            <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <pre className="flex-1 overflow-auto text-[0.8125rem] font-mono text-text-primary whitespace-pre-wrap">
          {text}
        </pre>
      </div>
    </div>
  );
}
