import { useEffect, useState } from "react";
import { fetchSessions } from "../api/schedule";
import { Spinner } from "../components/Spinner";
import type { Session } from "../types";

export function ScheduleList() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSessions({ status: "scheduled" })
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
        <div
          key={session.id}
          className="bg-bg-surface border border-border rounded-lg p-4"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="font-mono text-text-primary text-sm">
                {new Date(session.start_date).toLocaleDateString(undefined, {
                  weekday: "short",
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                })}
              </div>
              {session.end_date && (
                <div className="text-text-muted text-xs mt-0.5">
                  through{" "}
                  {new Date(session.end_date).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}
                </div>
              )}
            </div>
            <span
              className={`
                text-xs px-2 py-0.5 rounded font-medium
                ${
                  session.status === "scheduled"
                    ? "bg-accent/10 text-accent border border-accent/25"
                    : session.status === "completed"
                      ? "bg-success/10 text-success border border-success/25"
                      : "bg-warning/10 text-warning border border-warning/25"
                }
              `}
            >
              {session.status}
            </span>
          </div>

          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
            <span>Type: {session.session_type.replace("_", " ")}</span>
            {session.net_control_callsign && (
              <span>
                NCS:{" "}
                <span className="font-mono text-text-secondary">
                  {session.net_control_callsign}
                </span>
              </span>
            )}
            <span>Grace: {session.grace_period_hours}h</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function SchedulePage() {
  return (
    <div>
      <h1 className="text-xl font-bold text-text-primary mb-4">
        Net Schedule
      </h1>
      <ScheduleList />
    </div>
  );
}
