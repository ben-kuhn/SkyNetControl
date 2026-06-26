import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { AuthContext } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Spinner } from "../components/Spinner";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Modal } from "../components/Modal";
import { CheckInMap } from "../components/CheckInMap";
import type { CheckIn, CallbookResult, Session, UserRole } from "../types";
import {
  fetchSessionCheckins,
  scanMailbox,
  createManualCheckin,
  deleteCheckin,
  updateCheckin,
  reparseCheckin,
  reparseSession,
  approveSession,
  lookupCallsign,
  fetchRecentSessions,
  fetchModes,
} from "../api/checkins";

const canEdit = (role: UserRole) => role === "admin" || role === "net_control";

const parseStatusBadge: Record<string, { label: string; cls: string }> = {
  auto: { label: "auto", cls: "bg-success/10 text-success border border-success/25" },
  manual_review: { label: "manual review", cls: "bg-warning/15 text-warning border border-warning/30" },
  manually_entered: { label: "manual entry", cls: "bg-accent/10 text-accent border border-accent/25" },
};

const timingBadge: Record<string, { label: string; cls: string }> = {
  on_time: { label: "on time", cls: "bg-success/10 text-success border border-success/25" },
  early: { label: "early", cls: "bg-accent/10 text-accent border border-accent/25" },
  late: { label: "late", cls: "bg-warning/15 text-warning border border-warning/30" },
};

function formatSessionOption(s: Session): string {
  const d = new Date(s.start_date + "T00:00:00");
  const dateStr = d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  return `${dateStr} - ${s.session_type.replace(/_/g, " ")} (${s.status})`;
}

function SessionSelector({
  sessions,
  selectedId,
  onChange,
}: {
  sessions: Session[];
  selectedId: number | null;
  onChange: (id: number) => void;
}) {
  return (
    <div className="flex items-center gap-3 mb-4 flex-wrap">
      <label className="text-sm text-text-muted whitespace-nowrap">Session:</label>
      <select
        className="bg-bg-elevated border border-border text-text-primary px-3 py-2 rounded-md text-sm font-mono min-w-[340px] focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
        value={selectedId ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {sessions.length === 0 && <option value="">No sessions found</option>}
        {sessions.map((s) => (
          <option key={s.id} value={s.id}>
            {formatSessionOption(s)}
          </option>
        ))}
      </select>
      <Link to="/schedule" className="text-sm text-accent hover:text-accent-hover transition-colors">
        Show more...
      </Link>
    </div>
  );
}

function StatsBar({ checkins }: { checkins: CheckIn[] }) {
  const stats = useMemo(() => {
    let needsReview = 0, newMembers = 0, onTime = 0, early = 0, late = 0;
    for (const c of checkins) {
      if (c.parse_status === "manual_review") needsReview++;
      if (c.is_new_member) newMembers++;
      if (c.timing_status === "on_time") onTime++;
      else if (c.timing_status === "early") early++;
      else if (c.timing_status === "late") late++;
    }
    return { total: checkins.length, needsReview, newMembers, onTime, early, late };
  }, [checkins]);

  return (
    <div className="flex gap-6 mb-4 px-4 py-3 bg-bg-surface border border-border rounded-lg flex-wrap">
      <Stat value={stats.total} label="check-ins" />
      <Stat value={stats.needsReview} label="need review" color="text-warning" />
      <Stat value={stats.newMembers} label="new members" color="text-warning" />
      <Stat value={stats.onTime} label="on time" color="text-success" />
      <Stat value={stats.early} label="early" color="text-accent" />
      <Stat value={stats.late} label="late" color="text-warning" />
    </div>
  );
}

function Stat({ value, label, color }: { value: number; label: string; color?: string }) {
  return (
    <div className="flex items-center gap-1.5 text-[0.8125rem]">
      <span className={`font-semibold font-mono ${color || "text-text-primary"}`}>{value}</span>
      <span className="text-text-muted">{label}</span>
    </div>
  );
}

function CheckinTable({
  checkins,
  canEditCheckins,
  selectedCheckinId,
  onSelectCheckin,
  onEdit,
  onDelete,
}: {
  checkins: CheckIn[];
  canEditCheckins: boolean;
  selectedCheckinId: number | null;
  onSelectCheckin: (id: number | null) => void;
  onEdit: (c: CheckIn) => void;
  onDelete: (c: CheckIn) => void;
}) {
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());

  // Scroll selected row into view (when triggered by map pin click)
  useEffect(() => {
    if (selectedCheckinId) {
      const row = rowRefs.current.get(selectedCheckinId);
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
  }, [selectedCheckinId]);

  return (
    <div className="border border-border rounded-lg overflow-auto">
      <table className="w-full text-[0.8125rem] border-collapse">
        <thead className="bg-bg-elevated">
          <tr>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Callsign</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Name</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Location</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Mode</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Status</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Timing</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">New</th>
            {canEditCheckins && <th className="border-b border-border w-20"></th>}
          </tr>
        </thead>
        <tbody>
          {checkins.map((c) => {
            const isSelected = c.id === selectedCheckinId;
            return (
              <React.Fragment key={c.id}>
              <tr
                ref={(el) => { if (el) rowRefs.current.set(c.id, el); else rowRefs.current.delete(c.id); }}
                onClick={() => onSelectCheckin(isSelected ? null : c.id)}
                className={`${c.comments ? "" : "border-b border-border last:border-b-0"} cursor-pointer transition-colors ${
                  isSelected
                    ? "bg-accent/[0.08] border-l-2 border-l-accent"
                    : c.parse_status === "manual_review"
                      ? "bg-warning/[0.04] hover:bg-bg-elevated/50"
                      : "hover:bg-bg-elevated/50"
                }`}
              >
                <td className="px-3 py-2.5 font-mono font-semibold text-text-primary">{c.callsign}</td>
                <td className="px-3 py-2.5 text-text-secondary">{c.name}</td>
                <td className="px-3 py-2.5 text-text-secondary">
                  {[c.city, c.state].filter(Boolean).join(", ")}
                </td>
                <td className="px-3 py-2.5 text-text-secondary">{c.mode}</td>
                <td className="px-3 py-2.5">
                  <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${parseStatusBadge[c.parse_status]?.cls}`}>
                    {parseStatusBadge[c.parse_status]?.label}
                  </span>
                </td>
                <td className="px-3 py-2.5">
                  <span className={`inline-block text-[0.6875rem] px-2 py-0.5 rounded-full font-medium ${timingBadge[c.timing_status]?.cls}`}>
                    {timingBadge[c.timing_status]?.label}
                  </span>
                </td>
                <td className="px-3 py-2.5">
                  {c.is_new_member && <span className="text-warning" title="New member">&#9733;</span>}
                </td>
                {canEditCheckins && (
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1">
                      <button
                        onClick={(e) => { e.stopPropagation(); onEdit(c); }}
                        className="text-text-muted hover:text-accent transition-colors p-1 rounded"
                        aria-label={`Edit check-in for ${c.callsign}`}
                        title="Edit"
                      >
                        <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); onDelete(c); }}
                        className="text-text-muted hover:text-danger transition-colors p-1 rounded"
                        aria-label={`Delete check-in for ${c.callsign}`}
                        title="Delete"
                      >
                        <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3" />
                        </svg>
                      </button>
                    </div>
                  </td>
                )}
              </tr>
              {c.comments && (
                <tr
                  key={`${c.id}-comments`}
                  onClick={() => onSelectCheckin(isSelected ? null : c.id)}
                  className={`border-b border-border last:border-b-0 cursor-pointer transition-colors ${
                    isSelected
                      ? "bg-accent/[0.08]"
                      : c.parse_status === "manual_review"
                        ? "bg-warning/[0.04] hover:bg-bg-elevated/50"
                        : "hover:bg-bg-elevated/50"
                  }`}
                >
                  <td colSpan={canEditCheckins ? 8 : 7} className="px-3 pb-2.5 -mt-1 text-text-muted text-xs italic">
                    {c.comments}
                  </td>
                </tr>
              )}
            </React.Fragment>
            );
          })}
          {checkins.length === 0 && (
            <tr>
              <td colSpan={canEditCheckins ? 8 : 7} className="px-3 py-8 text-center text-text-muted text-sm">
                No check-ins for this session yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function CallsignLookupField({
  value,
  onChange,
  onLookupResult,
}: {
  value: string;
  onChange: (v: string) => void;
  onLookupResult: (result: CallbookResult) => void;
}) {
  const [lookingUp, setLookingUp] = useState(false);
  const [lookupMsg, setLookupMsg] = useState("");

  const handleLookup = async () => {
    if (!value.trim()) return;
    setLookingUp(true);
    setLookupMsg("");
    try {
      const result = await lookupCallsign(value.trim());
      onLookupResult(result);
      setLookupMsg("");
    } catch (err: any) {
      if (err.status === 404) {
        setLookupMsg("Not found in callbook");
      } else if (err.status === 503) {
        setLookupMsg("Callbook not configured");
      } else {
        setLookupMsg("Lookup failed");
      }
    } finally {
      setLookingUp(false);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-text-secondary">Callsign</label>
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
          value={value}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          placeholder="W0ABC"
        />
        <Button size="sm" variant="secondary" onClick={handleLookup} loading={lookingUp} type="button">
          Lookup
        </Button>
      </div>
      {lookupMsg && <p className="text-xs text-warning">{lookupMsg}</p>}
    </div>
  );
}

function AddCheckinModal({
  open,
  onClose,
  sessionId,
  onAdded,
  modes,
}: {
  open: boolean;
  onClose: () => void;
  sessionId: number;
  onAdded: () => void;
  modes: string[];
}) {
  const { addToast } = useToast();
  const emptyForm = { callsign: "", name: "", mode: "Voice", city: "", county: "", state: "", comments: "" };
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);

  const handleClose = () => {
    setForm(emptyForm);
    onClose();
  };

  const handleLookupResult = (result: CallbookResult) => {
    setForm((f) => ({
      ...f,
      name: result.name ?? "",
      city: result.city ?? "",
      county: result.county ?? "",
      state: result.state ?? "",
    }));
  };

  const handleSave = async () => {
    if (!form.callsign.trim() || !form.name.trim()) return;
    setSaving(true);
    try {
      await createManualCheckin({
        session_id: sessionId,
        callsign: form.callsign,
        name: form.name,
        mode: form.mode,
        city: form.city || undefined,
        county: form.county || undefined,
        state: form.state || undefined,
        comments: form.comments || undefined,
      });
      addToast("Check-in added", "success");
      setForm(emptyForm);
      onAdded();
      handleClose();
    } catch {
      addToast("Failed to add check-in", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={handleClose} title="Add Check-in">
      <div className="flex flex-col gap-3">
        <CallsignLookupField value={form.callsign} onChange={(v) => setForm((f) => ({ ...f, callsign: v }))} onLookupResult={handleLookupResult} />
        <Input label="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-text-secondary">Mode</label>
          <select
            className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
            value={form.mode}
            onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value }))}
          >
            {modes.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Input label="City" value={form.city} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} />
          <Input label="State" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))} />
        </div>
        <Input label="County" value={form.county} onChange={(e) => setForm((f) => ({ ...f, county: e.target.value }))} />
        <Input label="Comments" value={form.comments} onChange={(e) => setForm((f) => ({ ...f, comments: e.target.value }))} />
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={handleClose}>Cancel</Button>
          <Button onClick={handleSave} loading={saving}>Add Check-in</Button>
        </div>
      </div>
    </Modal>
  );
}

function EditCheckinModal({
  open,
  onClose,
  checkin,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  checkin: CheckIn | null;
  onSaved: () => void;
}) {
  const { addToast } = useToast();
  const [form, setForm] = useState({ callsign: "", name: "", mode: "", city: "", county: "", state: "", comments: "", parse_status: "auto" as CheckIn["parse_status"] });
  const [saving, setSaving] = useState(false);
  const [reparsing, setReparsing] = useState(false);

  useEffect(() => {
    if (checkin) {
      setForm({
        callsign: checkin.callsign,
        name: checkin.name,
        mode: checkin.mode,
        city: checkin.city || "",
        county: checkin.county || "",
        state: checkin.state || "",
        comments: checkin.comments || "",
        parse_status: checkin.parse_status,
      });
    }
  }, [checkin]);

  const handleLookupResult = (result: CallbookResult) => {
    setForm((f) => ({
      ...f,
      name: result.name ?? "",
      city: result.city ?? "",
      county: result.county ?? "",
      state: result.state ?? "",
    }));
  };

  const handleSave = async () => {
    if (!checkin) return;
    setSaving(true);
    try {
      await updateCheckin(checkin.id, {
        callsign: form.callsign,
        name: form.name,
        mode: form.mode,
        city: form.city,
        county: form.county,
        state: form.state,
        comments: form.comments,
        parse_status: form.parse_status,
      });
      addToast("Check-in updated", "success");
      onSaved();
      onClose();
    } catch {
      addToast("Failed to update check-in", "error");
    } finally {
      setSaving(false);
    }
  };

  const handleReparse = async () => {
    if (!checkin) return;
    setReparsing(true);
    try {
      const updated = await reparseCheckin(checkin.id);
      setForm({
        callsign: updated.callsign,
        name: updated.name,
        mode: updated.mode,
        city: updated.city || "",
        county: updated.county || "",
        state: updated.state || "",
        comments: updated.comments || "",
        parse_status: updated.parse_status,
      });
      addToast("Re-parsed from original message", "success");
      onSaved();
    } catch {
      addToast("Re-parse failed", "error");
    } finally {
      setReparsing(false);
    }
  };

  // Two-column desktop layout when there's a form view to render alongside
  // the editable fields — otherwise stay narrow. Mobile always stacks.
  const hasFormView = !!checkin?.form_view_html;
  const modalSize = hasFormView ? "xl" : "lg";

  const fieldsColumn = (
    <div className="flex flex-col gap-3">
      <CallsignLookupField value={form.callsign} onChange={(v) => setForm((f) => ({ ...f, callsign: v }))} onLookupResult={handleLookupResult} />
      <Input label="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
      <Input label="Mode" value={form.mode} onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value }))} />
      <div className="grid grid-cols-2 gap-3">
        <Input label="City" value={form.city} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} />
        <Input label="State" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))} />
      </div>
      <Input label="County" value={form.county} onChange={(e) => setForm((f) => ({ ...f, county: e.target.value }))} />
      <Input label="Comments" value={form.comments} onChange={(e) => setForm((f) => ({ ...f, comments: e.target.value }))} />
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-text-secondary">Parse Status</label>
        <select
          className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
          value={form.parse_status}
          onChange={(e) => setForm((f) => ({ ...f, parse_status: e.target.value as CheckIn["parse_status"] }))}
        >
          <option value="auto">Auto</option>
          <option value="manual_review">Manual Review</option>
          <option value="manually_entered">Manually Entered</option>
        </select>
      </div>
    </div>
  );

  const formViewColumn = checkin?.form_view_html && (
    <div className="flex flex-col gap-2 min-h-0 lg:col-span-2">
      <div className="text-xs font-medium text-text-secondary">Form view</div>
      <iframe
        sandbox=""
        srcDoc={checkin.form_view_html}
        className="w-full flex-1 min-h-[24rem] border border-border rounded bg-white"
        title="Winlink form view"
      />
    </div>
  );

  const rawMessagePanel = checkin?.raw_message && (
    <details className="bg-bg-elevated/50 rounded-md border border-border">
      <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-text-secondary hover:text-text-primary">
        Original message
      </summary>
      <div className="px-3 pb-3 flex flex-col gap-2">
        <div className="text-xs text-text-muted">
          <div><span className="font-medium">Subject:</span> {checkin.raw_message.subject}</div>
          <div><span className="font-medium">From:</span> {checkin.raw_message.from_address}</div>
          <div><span className="font-medium">Received:</span> {new Date(checkin.raw_message.received_at).toLocaleString()}</div>
        </div>
        <pre className="text-xs font-mono whitespace-pre-wrap bg-bg-base/60 border border-border rounded p-2 max-h-64 overflow-auto text-text-primary">
          {checkin.raw_message.body}
        </pre>
      </div>
    </details>
  );

  const footer = (
    <div className="flex flex-wrap justify-end gap-2">
      {checkin?.raw_message && (
        <Button variant="secondary" onClick={handleReparse} loading={reparsing}>
          Re-parse
        </Button>
      )}
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button onClick={handleSave} loading={saving}>Save</Button>
    </div>
  );

  return (
    <Modal open={open} onClose={onClose} title="Edit Check-in" size={modalSize} footer={footer}>
      {hasFormView ? (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {formViewColumn}
            {fieldsColumn}
          </div>
          {rawMessagePanel}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {rawMessagePanel}
          {fieldsColumn}
        </div>
      )}
    </Modal>
  );
}

export function CheckInsPage() {
  const { user } = useContext(AuthContext);
  const { addToast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [checkins, setCheckins] = useState<CheckIn[]>([]);
  const [loading, setLoading] = useState(true);
  const [checkinsLoading, setCheckinsLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [approving, setApproving] = useState(false);
  const [reparsingSession, setReparsingSession] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingCheckin, setEditingCheckin] = useState<CheckIn | null>(null);
  const [showApproveConfirm, setShowApproveConfirm] = useState(false);
  const [selectedCheckinId, setSelectedCheckinId] = useState<number | null>(null);
  const [modes, setModes] = useState<string[]>(["Voice", "Winlink", "CW", "Digital"]);
  const [notPublic, setNotPublic] = useState(false);

  const userCanEdit = user ? canEdit(user.role) : false;

  // Filter sessions: anonymous users can only see completed sessions
  const visibleSessions = useMemo(() => {
    if (user) return sessions;
    return sessions.filter((s) => s.status === "completed");
  }, [sessions, user]);

  const initialSessionParam = searchParams.get("session");

  const loadSessions = useCallback(async (extraSessionId?: number) => {
    const all = await fetchRecentSessions();
    const sorted = [...all].sort((a, b) => b.start_date.localeCompare(a.start_date));

    const now = new Date().toISOString().split("T")[0]!;

    // Prefer a session that's currently in window (today falls between
    // start_date and end_date inclusive) — for week-long nets the active
    // session shouldn't get bumped to "next week" on its final day.
    const currentScheduled = sorted.find(
      (s) =>
        s.status === "scheduled" &&
        s.start_date <= now &&
        (s.end_date == null ? s.start_date === now : s.end_date >= now),
    );

    // Otherwise fall back to the earliest strictly-future scheduled session.
    const nextScheduled = sorted
      .filter((s) => s.status === "scheduled" && s.start_date > now)
      .pop();

    // Take 7 most recent
    const recent = sorted.slice(0, 7);

    // Merge: include current + next-scheduled if not already in recent
    const sessionMap = new Map(recent.map((s) => [s.id, s]));
    if (currentScheduled) sessionMap.set(currentScheduled.id, currentScheduled);
    if (nextScheduled) sessionMap.set(nextScheduled.id, nextScheduled);

    // Include extra session (from query param or current selection)
    const extraId = extraSessionId ?? (initialSessionParam ? Number(initialSessionParam) : null);
    if (extraId) {
      const extra = sorted.find((s) => s.id === extraId);
      if (extra) sessionMap.set(extra.id, extra);
    }

    const finalSessions = [...sessionMap.values()].sort((a, b) => b.start_date.localeCompare(a.start_date));
    setSessions(finalSessions);
    return { finalSessions, currentScheduled, nextScheduled };
  }, [initialSessionParam]);

  useEffect(() => {
    if (!user) return;
    fetchModes()
      .then(setModes)
      .catch(() => {});
  }, [user]);

  // Load sessions on mount
  useEffect(() => {
    loadSessions()
      .then(({ finalSessions, currentScheduled, nextScheduled }) => {
        let defaultId: number | null = null;
        if (initialSessionParam) {
          defaultId = Number(initialSessionParam);
        } else if (user) {
          // Authenticated users: don't jump to a future session until it
          // actually starts. Preference order:
          //   1. recent past session whose roster is still unfinished —
          //      rosters get drafted *after* the net, so the previous
          //      session needs to stay pinned even after its window ends
          //      and the next session's window opens (backlog item 3).
          //      Within ROSTER_GRACE_DAYS for no-roster-yet; indefinitely
          //      while the roster is in DRAFT/APPROVED.
          //   2. session currently in window (start_date <= today <= end_date)
          //   3. most recent past session (start_date <= today), any status
          //   4. next-scheduled future session (only when there's no history)
          const ROSTER_GRACE_DAYS = 7;
          const today = new Date().toISOString().split("T")[0]!;
          const daysBetween = (from: string, to: string) =>
            Math.floor((Date.parse(to) - Date.parse(from)) / 86400000);
          const isRosterDone = (s: typeof finalSessions[number]) =>
            s.roster_status === "sent" || s.roster_status === "skipped";
          const needsRosterAttention = (s: typeof finalSessions[number]) => {
            const endDate = s.end_date ?? s.start_date;
            if (endDate >= today) return false; // not actually past yet
            if (isRosterDone(s)) return false;
            if (s.roster_status != null) return true; // DRAFT/APPROVED — stay
            return daysBetween(endDate, today) <= ROSTER_GRACE_DAYS;
          };
          const pendingPastSession = finalSessions.find(needsRosterAttention);

          if (pendingPastSession) {
            defaultId = pendingPastSession.id;
          } else if (currentScheduled) {
            defaultId = currentScheduled.id;
          } else {
            const mostRecentPast = finalSessions.find((s) => s.start_date <= today);
            if (mostRecentPast) {
              defaultId = mostRecentPast.id;
            } else if (nextScheduled) {
              defaultId = nextScheduled.id;
            } else {
              defaultId = finalSessions[0]?.id ?? null;
            }
          }
        } else {
          // Anonymous users: only show completed sessions
          const recentCompleted = finalSessions.find((s) => s.status === "completed");
          defaultId = recentCompleted?.id ?? null;
        }
        setSelectedSessionId(defaultId);
      })
      .catch(() => addToast("Failed to load sessions", "error"))
      .finally(() => setLoading(false));
  }, [loadSessions, addToast, initialSessionParam, user]);

  // Load checkins when session changes
  const loadCheckins = useCallback(async () => {
    if (!selectedSessionId) {
      setCheckins([]);
      setNotPublic(false);
      return;
    }
    setCheckinsLoading(true);
    setNotPublic(false);
    try {
      const data = await fetchSessionCheckins(selectedSessionId);
      setCheckins(data);
    } catch (err: any) {
      if (err?.status === 404) {
        setNotPublic(true);
        setCheckins([]);
      } else {
        addToast("Failed to load check-ins", "error");
      }
    } finally {
      setCheckinsLoading(false);
    }
  }, [selectedSessionId, addToast]);

  useEffect(() => {
    loadCheckins();
    // Update selected session object
    const s = sessions.find((s) => s.id === selectedSessionId) || null;
    setSelectedSession(s);
  }, [selectedSessionId, sessions, loadCheckins]);

  const handleSessionChange = (id: number) => {
    setSelectedSessionId(id);
    setSelectedCheckinId(null);
    setSearchParams({ session: String(id) });
  };

  const handleScan = async () => {
    if (!selectedSessionId) return;
    setScanning(true);
    try {
      const result = await scanMailbox(selectedSessionId);
      addToast(`Imported ${result.imported} check-in${result.imported !== 1 ? "s" : ""}`, "success");
      await loadCheckins();
    } catch {
      addToast("Scan failed", "error");
    } finally {
      setScanning(false);
    }
  };

  const handleReparseSession = async () => {
    if (!selectedSessionId) return;
    const ok = window.confirm(
      "Re-parse every check-in for this session and reclaim any deleted ones whose original message is still on file?",
    );
    if (!ok) return;
    setReparsingSession(true);
    try {
      const result = await reparseSession(selectedSessionId);
      const parts: string[] = [];
      if (result.updated) parts.push(`re-parsed ${result.updated}`);
      if (result.imported) parts.push(`reclaimed ${result.imported}`);
      addToast(parts.length ? parts.join(", ") : "nothing to do", "success");
      await loadCheckins();
    } catch {
      addToast("Re-parse failed", "error");
    } finally {
      setReparsingSession(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedSessionId) return;
    setApproving(true);
    try {
      const result = await approveSession(selectedSessionId);
      addToast(`Session approved. ${result.members_updated} member records updated.`, "success");
      await loadSessions(selectedSessionId);
      await loadCheckins();
    } catch {
      addToast("Approve failed", "error");
    } finally {
      setApproving(false);
      setShowApproveConfirm(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  const isCompleted = selectedSession?.status === "completed";
  const isCancelled = selectedSession?.status === "cancelled";

  return (
    <div>
      <h1 className="text-xl font-bold text-text-primary mb-4">Check-ins</h1>

      <SessionSelector sessions={visibleSessions} selectedId={selectedSessionId} onChange={handleSessionChange} />

      {userCanEdit && selectedSessionId && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Button size="sm" onClick={handleScan} loading={scanning}>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            Scan Mailbox
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setShowAddModal(true)}>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Add Check-in
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={handleReparseSession}
            loading={reparsingSession}
            disabled={isCompleted || isCancelled}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Re-parse all
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setShowApproveConfirm(true)}
            disabled={isCompleted || isCancelled}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            Approve Session
          </Button>
        </div>
      )}

      {selectedSessionId && !checkinsLoading && !notPublic && <StatsBar checkins={checkins} />}

      {notPublic ? (
        <p className="text-text-muted text-sm py-8 text-center">
          This session is not yet available for public viewing.
        </p>
      ) : checkinsLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : selectedSessionId ? (
        <div className="flex flex-col lg:flex-row gap-4">
          <div className="flex-1 min-w-0">
            <CheckinTable
              checkins={checkins}
              canEditCheckins={userCanEdit}
              selectedCheckinId={selectedCheckinId}
              onSelectCheckin={setSelectedCheckinId}
              onEdit={setEditingCheckin}
              onDelete={async (c) => {
                if (!window.confirm(`Delete check-in for ${c.callsign}? This can't be undone.`)) return;
                try {
                  await deleteCheckin(c.id);
                  addToast(`Deleted check-in for ${c.callsign}`, "success");
                  loadCheckins();
                } catch {
                  addToast("Failed to delete check-in", "error");
                }
              }}
            />
          </div>
          <div className="flex-1 min-h-[400px]">
            <CheckInMap
              checkins={checkins}
              selectedCheckinId={selectedCheckinId}
              onSelectCheckin={setSelectedCheckinId}
            />
          </div>
        </div>
      ) : (
        <p className="text-text-muted text-sm py-4">Select a session above to view check-ins.</p>
      )}

      {selectedSessionId && (
        <AddCheckinModal open={showAddModal} onClose={() => setShowAddModal(false)} sessionId={selectedSessionId} onAdded={loadCheckins} modes={modes} />
      )}

      <EditCheckinModal open={editingCheckin !== null} onClose={() => setEditingCheckin(null)} checkin={editingCheckin} onSaved={loadCheckins} />

      <Modal open={showApproveConfirm} onClose={() => setShowApproveConfirm(false)} title="Approve Session">
        <p className="text-sm text-text-secondary mb-4">
          Approve all check-ins and mark this session as completed? This updates member records.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setShowApproveConfirm(false)}>Cancel</Button>
          <Button onClick={handleApprove} loading={approving}>Approve</Button>
        </div>
      </Modal>
    </div>
  );
}
