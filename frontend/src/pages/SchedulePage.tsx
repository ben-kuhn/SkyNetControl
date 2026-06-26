import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  createSeason,
  createSession,
  deleteSeason,
  fetchSeasons,
  fetchSessions,
  updateSession,
} from "../api/schedule";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Modal } from "../components/Modal";
import { Spinner } from "../components/Spinner";
import { useToast } from "../context/ToastContext";
import { useAuth } from "../hooks/useAuth";
import type {
  NetRole,
  Season,
  Session,
  SessionStatus,
  SessionType,
} from "../types";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const SESSION_TYPE_OPTIONS: { value: SessionType; label: string }[] = [
  { value: "regular_checkin", label: "Regular check-in" },
  { value: "activity", label: "Activity" },
  { value: "real_event", label: "Real event" },
];

const SESSION_STATUS_OPTIONS: { value: SessionStatus; label: string }[] = [
  { value: "scheduled", label: "Scheduled" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
];

function canEditSessions(role: NetRole | "admin" | null | undefined): boolean {
  return role === "admin" || role === "net_control";
}

function canManageSeasons(role: NetRole | "admin" | null | undefined): boolean {
  return role === "admin";
}

function statusBadgeClass(status: string): string {
  if (status === "scheduled")
    return "bg-accent/10 text-accent border border-accent/25";
  if (status === "completed")
    return "bg-success/10 text-success border border-success/25";
  return "bg-warning/10 text-warning border border-warning/25";
}

function formatDate(value: string, opts?: Intl.DateTimeFormatOptions): string {
  return new Date(value).toLocaleDateString(undefined, opts);
}

function SessionCard({
  session,
  canEdit,
  onEdit,
}: {
  session: Session;
  canEdit: boolean;
  onEdit: () => void;
}) {
  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="font-mono text-text-primary text-sm">
            {formatDate(session.start_date, {
              weekday: "short",
              year: "numeric",
              month: "short",
              day: "numeric",
            })}
          </div>
          {session.end_date && (
            <div className="text-text-muted text-xs mt-0.5">
              through {formatDate(session.end_date, { month: "short", day: "numeric" })}
            </div>
          )}
        </div>
        <span
          className={`text-xs px-2 py-0.5 rounded font-medium ${statusBadgeClass(session.status)}`}
        >
          {session.status}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
        <span>Type: {session.session_type.replace(/_/g, " ")}</span>
        {session.net_control_callsign && (
          <span>
            NCS:{" "}
            <span className="font-mono text-text-secondary">
              {session.net_control_callsign}
            </span>
          </span>
        )}
        <span>Grace: {session.grace_period_hours}h</span>
        <Link
          to={`/checkins?session=${session.id}`}
          className="text-accent hover:text-accent-hover transition-colors"
        >
          View check-ins
        </Link>
        {canEdit && (
          <button
            onClick={onEdit}
            className="text-accent hover:text-accent-hover transition-colors"
          >
            Edit
          </button>
        )}
      </div>
    </div>
  );
}

export function ScheduleList() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // ScheduleList is used by PendingPage (no net context) — use default slug with scheduled filter
    fetchSessions(undefined, { status: "scheduled" })
      .then(setSessions)
      .catch(() => setError("Failed to load sessions"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return <p className="text-danger text-sm">{error}</p>;
  }

  if (sessions.length === 0) {
    return (
      <p className="text-text-muted text-sm py-4">
        No upcoming sessions scheduled.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {sessions.map((session) => (
        <SessionCard key={session.id} session={session} canEdit={false} onEdit={() => {}} />
      ))}
    </div>
  );
}

function CreateSeasonModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { addToast } = useToast();
  const emptyForm = {
    name: "",
    start_date: "",
    end_date: "",
    is_week_long: false,
    day_of_week: "0",
    time: "19:00",
    activity_cadence: "2",
    default_net_control_callsign: "",
  };
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);

  const handleClose = () => {
    setForm(emptyForm);
    onClose();
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.start_date || !form.end_date) {
      addToast("Name, start date, and end date are required", "error");
      return;
    }
    setSaving(true);
    try {
      await createSeason({
        name: form.name.trim(),
        start_date: form.start_date,
        end_date: form.end_date,
        is_week_long: form.is_week_long,
        day_of_week: form.is_week_long ? null : Number(form.day_of_week),
        time: form.is_week_long ? null : form.time || null,
        activity_cadence: Number(form.activity_cadence),
        default_net_control_callsign:
          form.default_net_control_callsign.trim() || null,
      });
      addToast("Season created", "success");
      setForm(emptyForm);
      onCreated();
      onClose();
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Failed to create season",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={handleClose} title="New Season">
      <div className="flex flex-col gap-3">
        <Input
          label="Name"
          placeholder="Spring 2026"
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
        />
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Start date"
            type="date"
            value={form.start_date}
            onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))}
          />
          <Input
            label="End date"
            type="date"
            value={form.end_date}
            onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))}
          />
        </div>

        <label className="flex items-center gap-2 text-sm text-text-secondary">
          <input
            type="checkbox"
            checked={form.is_week_long}
            onChange={(e) =>
              setForm((f) => ({ ...f, is_week_long: e.target.checked }))
            }
          />
          Week-long sessions (one rolling session per week)
        </label>

        {!form.is_week_long && (
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-text-secondary">
                Day of week
              </label>
              <select
                className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
                value={form.day_of_week}
                onChange={(e) =>
                  setForm((f) => ({ ...f, day_of_week: e.target.value }))
                }
              >
                {DAY_NAMES.map((name, idx) => (
                  <option key={idx} value={String(idx)}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <Input
              label="Time (HH:MM)"
              placeholder="19:00"
              value={form.time}
              onChange={(e) => setForm((f) => ({ ...f, time: e.target.value }))}
            />
          </div>
        )}

        <Input
          label="Activity cadence"
          type="number"
          min={0}
          value={form.activity_cadence}
          onChange={(e) =>
            setForm((f) => ({ ...f, activity_cadence: e.target.value }))
          }
        />
        <p className="text-xs text-text-muted -mt-1">
          0 = no activities. Otherwise every Nth session is an activity (the
          second session in each block of N).
        </p>

        <Input
          label="Default net control operator (optional)"
          placeholder="KD0NCO"
          value={form.default_net_control_callsign}
          onChange={(e) =>
            setForm((f) => ({ ...f, default_net_control_callsign: e.target.value }))
          }
        />
        <p className="text-xs text-text-muted -mt-1">
          Stamped onto every session in this season. Leave blank if NCOs
          rotate — you can set them per-session instead.
        </p>

        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={handleClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} loading={saving}>
            Create season
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function CreateSessionModal({
  open,
  onClose,
  onCreated,
  seasons,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  seasons: Season[];
}) {
  const { addToast } = useToast();
  const emptyForm = {
    start_date: "",
    end_date: "",
    session_type: "regular_checkin" as SessionType,
    season_id: "",
    grace_period_hours: "24",
    net_control_callsign: "",
  };
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);

  const handleClose = () => {
    setForm(emptyForm);
    onClose();
  };

  const handleSave = async () => {
    if (!form.start_date) {
      addToast("Start date is required", "error");
      return;
    }
    setSaving(true);
    try {
      await createSession({
        start_date: form.start_date,
        end_date: form.end_date || null,
        session_type: form.session_type,
        season_id: form.season_id ? Number(form.season_id) : null,
        grace_period_hours: Number(form.grace_period_hours) || 24,
        net_control_callsign: form.net_control_callsign.trim() || null,
      });
      addToast("Session created", "success");
      setForm(emptyForm);
      onCreated();
      onClose();
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Failed to create session",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={handleClose} title="Add Session">
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Start date"
            type="date"
            value={form.start_date}
            onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))}
          />
          <Input
            label="End date (optional)"
            type="date"
            value={form.end_date}
            onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-text-secondary">Type</label>
          <select
            className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
            value={form.session_type}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                session_type: e.target.value as SessionType,
                season_id: e.target.value === "real_event" ? "" : f.season_id,
              }))
            }
          >
            {SESSION_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {form.session_type !== "real_event" && (
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-text-secondary">
              Season (optional)
            </label>
            <select
              className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
              value={form.season_id}
              onChange={(e) =>
                setForm((f) => ({ ...f, season_id: e.target.value }))
              }
            >
              <option value="">— None —</option>
              {seasons.map((s) => (
                <option key={s.id} value={String(s.id)}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Grace period (hours)"
            type="number"
            min={0}
            step={0.5}
            value={form.grace_period_hours}
            onChange={(e) =>
              setForm((f) => ({ ...f, grace_period_hours: e.target.value }))
            }
          />
          <Input
            label="Net control callsign"
            mono
            placeholder="WAØXYZ"
            value={form.net_control_callsign}
            onChange={(e) =>
              setForm((f) => ({ ...f, net_control_callsign: e.target.value }))
            }
          />
        </div>

        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={handleClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} loading={saving}>
            Add session
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function EditSessionModal({
  open,
  onClose,
  session,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  session: Session | null;
  onSaved: () => void;
}) {
  const { addToast } = useToast();
  const [form, setForm] = useState({
    status: "scheduled" as SessionStatus,
    session_type: "regular_checkin" as SessionType,
    net_control_callsign: "",
    grace_period_hours: "24",
    end_date: "",
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (session) {
      setForm({
        status: session.status as SessionStatus,
        session_type: session.session_type as SessionType,
        net_control_callsign: session.net_control_callsign ?? "",
        grace_period_hours: String(session.grace_period_hours),
        end_date: session.end_date ?? "",
      });
    }
  }, [session]);

  const handleSave = async () => {
    if (!session) return;
    setSaving(true);
    try {
      await updateSession(session.id, {
        status: form.status,
        session_type: form.session_type,
        net_control_callsign: form.net_control_callsign.trim() || null,
        grace_period_hours: Number(form.grace_period_hours) || 24,
        end_date: form.end_date || null,
      });
      addToast("Session updated", "success");
      onSaved();
      onClose();
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Failed to update session",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Edit Session">
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-text-secondary">Status</label>
          <select
            className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
            value={form.status}
            onChange={(e) =>
              setForm((f) => ({ ...f, status: e.target.value as SessionStatus }))
            }
          >
            {SESSION_STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-text-secondary">Type</label>
          <select
            className="rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent"
            value={form.session_type}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                session_type: e.target.value as SessionType,
              }))
            }
          >
            {SESSION_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <Input
          label="Net control callsign"
          mono
          value={form.net_control_callsign}
          onChange={(e) =>
            setForm((f) => ({ ...f, net_control_callsign: e.target.value }))
          }
        />
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Grace period (hours)"
            type="number"
            min={0}
            step={0.5}
            value={form.grace_period_hours}
            onChange={(e) =>
              setForm((f) => ({ ...f, grace_period_hours: e.target.value }))
            }
          />
          <Input
            label="End date"
            type="date"
            value={form.end_date}
            onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))}
          />
        </div>

        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} loading={saving}>
            Save
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function SeasonRow({
  season,
  canDelete,
  onDelete,
}: {
  season: Season;
  canDelete: boolean;
  onDelete: () => void;
}) {
  const cadenceLabel =
    season.activity_cadence > 0
      ? `every ${season.activity_cadence} sessions`
      : "no activities";
  const scheduleLabel = season.is_week_long
    ? "week-long sessions"
    : `${season.day_of_week !== null ? DAY_NAMES[season.day_of_week] : "—"}${
        season.time ? ` @ ${season.time}` : ""
      }`;

  return (
    <div className="bg-bg-surface border border-border rounded-lg p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-text-primary font-medium">{season.name}</div>
          <div className="text-text-muted text-xs mt-0.5">
            {formatDate(season.start_date, { month: "short", day: "numeric", year: "numeric" })}
            {" – "}
            {formatDate(season.end_date, { month: "short", day: "numeric", year: "numeric" })}
          </div>
        </div>
        {canDelete && (
          <Button size="sm" variant="danger" onClick={onDelete}>
            Delete
          </Button>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
        <span>{scheduleLabel}</span>
        <span>Activities: {cadenceLabel}</span>
        <span>{season.sessions.length} sessions</span>
      </div>
    </div>
  );
}

export function SchedulePage() {
  const { user } = useAuth();
  const { addToast } = useToast();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [seasons, setSeasons] = useState<Season[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSeasonModal, setShowSeasonModal] = useState(false);
  const [showSessionModal, setShowSessionModal] = useState(false);
  const [editingSession, setEditingSession] = useState<Session | null>(null);

  // Task 13: role-based gating now comes from CurrentNetContext (Task 14 wires slug).
  // For now derive edit capability from is_admin flag (net_control wired in Task 14).
  const effectiveRole: "admin" | null = user?.is_admin ? "admin" : null;
  const editSessions = canEditSessions(effectiveRole);
  const manageSeasons = canManageSeasons(effectiveRole);

  const loadData = useCallback(() => {
    setLoading(true);
    setError(null);
    const seasonsCall: Promise<Season[]> = editSessions
      ? fetchSeasons()
      : Promise.resolve([]);
    Promise.all([fetchSessions(), seasonsCall])
      .then(([s, seas]) => {
        setSessions(s);
        setSeasons(seas);
      })
      .catch(() => setError("Failed to load schedule"))
      .finally(() => setLoading(false));
  }, [editSessions]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Split into upcoming vs. past so operators can scroll back to a
  // finished net (e.g. to finalize its roster) — page previously fetched
  // only `status=scheduled` and hid every completed/cancelled session.
  const { upcomingSessions, pastSessions } = useMemo(() => {
    const today = new Date().toISOString().split("T")[0]!;
    const isUpcoming = (s: Session) =>
      s.status === "scheduled" && (s.end_date ?? s.start_date) >= today;
    const upcoming = sessions
      .filter(isUpcoming)
      .sort((a, b) => a.start_date.localeCompare(b.start_date));
    const past = sessions
      .filter((s) => !isUpcoming(s))
      .sort((a, b) => b.start_date.localeCompare(a.start_date));
    return { upcomingSessions: upcoming, pastSessions: past };
  }, [sessions]);

  const sortedSeasons = useMemo(
    () =>
      [...seasons].sort(
        (a, b) =>
          new Date(b.start_date).getTime() - new Date(a.start_date).getTime(),
      ),
    [seasons],
  );

  const handleDeleteSeason = async (season: Season) => {
    const completedCount = season.sessions.filter((s) => s.status === "completed").length;
    const upcomingCount = season.sessions.length - completedCount;
    const lines = [`Delete season "${season.name}"?`, ""];
    if (upcomingCount > 0) {
      lines.push(`• ${upcomingCount} upcoming session(s) will be deleted.`);
    }
    if (completedCount > 0) {
      lines.push(`• ${completedCount} completed session(s) will be kept as standalone history.`);
    }
    lines.push("", "This cannot be undone.");
    if (!window.confirm(lines.join("\n"))) {
      return;
    }
    try {
      await deleteSeason(season.id);
      addToast("Season deleted", "success");
      loadData();
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : "Failed to delete season",
        "error",
      );
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <h1 className="text-xl font-bold text-text-primary">Net Schedule</h1>
        {editSessions && (
          <div className="flex gap-2">
            {manageSeasons && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setShowSeasonModal(true)}
              >
                + New Season
              </Button>
            )}
            <Button size="sm" onClick={() => setShowSessionModal(true)}>
              + Add Session
            </Button>
          </div>
        )}
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : error ? (
        <p className="text-danger text-sm">{error}</p>
      ) : (
        <>
          <section>
            <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2">
              Upcoming sessions
            </h2>
            {upcomingSessions.length === 0 ? (
              <p className="text-text-muted text-sm py-4">
                No upcoming sessions scheduled.
                {manageSeasons && " Create a season to auto-generate them."}
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {upcomingSessions.map((session) => (
                  <SessionCard
                    key={session.id}
                    session={session}
                    canEdit={editSessions}
                    onEdit={() => setEditingSession(session)}
                  />
                ))}
              </div>
            )}
          </section>

          {pastSessions.length > 0 && (
            <section className="mt-8">
              <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2">
                Past sessions
              </h2>
              <div className="flex flex-col gap-3">
                {pastSessions.map((session) => (
                  <SessionCard
                    key={session.id}
                    session={session}
                    canEdit={editSessions}
                    onEdit={() => setEditingSession(session)}
                  />
                ))}
              </div>
            </section>
          )}

          {manageSeasons && (
            <section className="mt-8">
              <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2">
                Seasons
              </h2>
              {sortedSeasons.length === 0 ? (
                <p className="text-text-muted text-sm py-4">
                  No seasons yet. Create one to auto-generate weekly sessions.
                </p>
              ) : (
                <div className="flex flex-col gap-3">
                  {sortedSeasons.map((season) => (
                    <SeasonRow
                      key={season.id}
                      season={season}
                      canDelete={manageSeasons}
                      onDelete={() => handleDeleteSeason(season)}
                    />
                  ))}
                </div>
              )}
            </section>
          )}
        </>
      )}

      <CreateSeasonModal
        open={showSeasonModal}
        onClose={() => setShowSeasonModal(false)}
        onCreated={loadData}
      />
      <CreateSessionModal
        open={showSessionModal}
        onClose={() => setShowSessionModal(false)}
        onCreated={loadData}
        seasons={seasons}
      />
      <EditSessionModal
        open={editingSession !== null}
        onClose={() => setEditingSession(null)}
        session={editingSession}
        onSaved={loadData}
      />
    </div>
  );
}
