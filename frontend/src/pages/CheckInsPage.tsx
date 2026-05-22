import { useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { AuthContext } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Spinner } from "../components/Spinner";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Modal } from "../components/Modal";
import type { CheckIn, CallbookResult, Session, UserRole } from "../types";
import {
  fetchSessionCheckins,
  scanMailbox,
  createManualCheckin,
  updateCheckin,
  approveSession,
  lookupCallsign,
  fetchRecentSessions,
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
  onEdit,
}: {
  checkins: CheckIn[];
  canEditCheckins: boolean;
  onEdit: (c: CheckIn) => void;
}) {
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <table className="w-full text-[0.8125rem] border-collapse">
        <thead className="bg-bg-elevated">
          <tr>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Callsign</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Name</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Location</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Mode</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Parse Status</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Timing</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">New</th>
            <th className="text-left px-3 py-2.5 font-semibold text-text-muted text-xs uppercase tracking-wider border-b border-border">Comments</th>
            {canEditCheckins && <th className="border-b border-border w-10"></th>}
          </tr>
        </thead>
        <tbody>
          {checkins.map((c) => (
            <tr
              key={c.id}
              className={`border-b border-border last:border-b-0 hover:bg-bg-elevated/50 ${c.parse_status === "manual_review" ? "bg-warning/[0.04]" : ""}`}
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
              <td className="px-3 py-2.5 max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap text-text-muted text-xs">
                {c.comments}
              </td>
              {canEditCheckins && (
                <td className="px-3 py-2.5">
                  <button
                    onClick={() => onEdit(c)}
                    className="text-text-muted hover:text-accent transition-colors p-1 rounded"
                    title="Edit"
                  >
                    <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                  </button>
                </td>
              )}
            </tr>
          ))}
          {checkins.length === 0 && (
            <tr>
              <td colSpan={canEditCheckins ? 9 : 8} className="px-3 py-8 text-center text-text-muted text-sm">
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
}: {
  open: boolean;
  onClose: () => void;
  sessionId: number;
  onAdded: () => void;
}) {
  const { addToast } = useToast();
  const [form, setForm] = useState({ callsign: "", name: "", mode: "Voice", city: "", county: "", state: "", comments: "" });
  const [saving, setSaving] = useState(false);

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
      setForm({ callsign: "", name: "", mode: "Voice", city: "", county: "", state: "", comments: "" });
      onAdded();
      onClose();
    } catch {
      addToast("Failed to add check-in", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Add Check-in">
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
            <option>Voice</option>
            <option>Winlink</option>
            <option>CW</option>
            <option>Digital</option>
          </select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Input label="City" value={form.city} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} />
          <Input label="State" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))} />
        </div>
        <Input label="County" value={form.county} onChange={(e) => setForm((f) => ({ ...f, county: e.target.value }))} />
        <Input label="Comments" value={form.comments} onChange={(e) => setForm((f) => ({ ...f, comments: e.target.value }))} />
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
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

  return (
    <Modal open={open} onClose={onClose} title="Edit Check-in">
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
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave} loading={saving}>Save</Button>
        </div>
      </div>
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
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingCheckin, setEditingCheckin] = useState<CheckIn | null>(null);
  const [showApproveConfirm, setShowApproveConfirm] = useState(false);

  const userCanEdit = user ? canEdit(user.role) : false;

  // Load sessions
  useEffect(() => {
    fetchRecentSessions()
      .then((all) => {
        // Sort by start_date desc
        const sorted = [...all].sort((a, b) => b.start_date.localeCompare(a.start_date));

        // Find the next scheduled session
        const now = new Date().toISOString().split("T")[0]!;
        const nextScheduled = sorted
          .filter((s) => s.status === "scheduled" && s.start_date >= now)
          .pop(); // earliest future scheduled

        // Take 7 most recent
        const recent = sorted.slice(0, 7);

        // Merge: include nextScheduled if not already in recent
        const sessionMap = new Map(recent.map((s) => [s.id, s]));
        if (nextScheduled) sessionMap.set(nextScheduled.id, nextScheduled);

        // Check for ?session= param
        const paramId = searchParams.get("session");
        if (paramId) {
          const paramSession = sorted.find((s) => s.id === Number(paramId));
          if (paramSession) sessionMap.set(paramSession.id, paramSession);
        }

        // Sort final list by date desc
        const finalSessions = [...sessionMap.values()].sort((a, b) => b.start_date.localeCompare(a.start_date));
        setSessions(finalSessions);

        // Select default
        let defaultId: number | null = null;
        if (paramId) {
          defaultId = Number(paramId);
        } else if (nextScheduled) {
          defaultId = nextScheduled.id;
        } else {
          const recentCompleted = finalSessions.find((s) => s.status === "completed");
          defaultId = recentCompleted?.id ?? finalSessions[0]?.id ?? null;
        }
        setSelectedSessionId(defaultId);
      })
      .catch(() => addToast("Failed to load sessions", "error"))
      .finally(() => setLoading(false));
  }, []);

  // Load checkins when session changes
  const loadCheckins = useCallback(async () => {
    if (!selectedSessionId) {
      setCheckins([]);
      return;
    }
    setCheckinsLoading(true);
    try {
      const data = await fetchSessionCheckins(selectedSessionId);
      setCheckins(data);
    } catch {
      addToast("Failed to load check-ins", "error");
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
    setSearchParams({ session: String(id) });
  };

  const handleScan = async () => {
    if (!selectedSessionId) return;
    setScanning(true);
    try {
      const result = await scanMailbox(selectedSessionId);
      addToast(`Imported ${result.imported} check-in${result.imported !== 1 ? "s" : ""}`, "success");
      loadCheckins();
    } catch {
      addToast("Scan failed", "error");
    } finally {
      setScanning(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedSessionId) return;
    setApproving(true);
    try {
      const result = await approveSession(selectedSessionId);
      addToast(`Session approved. ${result.members_updated} member records updated.`, "success");
      // Refresh sessions list to get updated status
      const all = await fetchRecentSessions();
      const sorted = [...all].sort((a, b) => b.start_date.localeCompare(a.start_date));
      const sessionMap = new Map(sorted.slice(0, 7).map((s) => [s.id, s]));
      const paramSession = sorted.find((s) => s.id === selectedSessionId);
      if (paramSession) sessionMap.set(paramSession.id, paramSession);
      setSessions([...sessionMap.values()].sort((a, b) => b.start_date.localeCompare(a.start_date)));
      loadCheckins();
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

      <SessionSelector sessions={sessions} selectedId={selectedSessionId} onChange={handleSessionChange} />

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

      {selectedSessionId && !checkinsLoading && <StatsBar checkins={checkins} />}

      {checkinsLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : selectedSessionId ? (
        <CheckinTable checkins={checkins} canEditCheckins={userCanEdit} onEdit={setEditingCheckin} />
      ) : (
        <p className="text-text-muted text-sm py-4">Select a session above to view check-ins.</p>
      )}

      {selectedSessionId && (
        <AddCheckinModal open={showAddModal} onClose={() => setShowAddModal(false)} sessionId={selectedSessionId} onAdded={loadCheckins} />
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
