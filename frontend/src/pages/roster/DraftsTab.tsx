import { useEffect, useMemo, useState } from "react";
import { fetchRosters } from "../../api/roster";
import type { Roster, RosterStatus } from "../../types";

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

export function DraftsTab() {
  const [rosters, setRosters] = useState<Roster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<RosterStatus>("draft");

  useEffect(() => {
    fetchRosters()
      .then((data) => {
        setRosters(data);
        setError(null);
      })
      .catch((e) => setError(e?.message ?? "Failed to load rosters"))
      .finally(() => setLoading(false));
  }, []);

  const counts = useMemo(() => {
    const c: Record<RosterStatus, number> = { draft: 0, approved: 0, sent: 0, skipped: 0 };
    for (const r of rosters) c[r.status]++;
    return c;
  }, [rosters]);

  const visible = useMemo(
    () => rosters.filter((r) => r.status === statusFilter),
    [rosters, statusFilter],
  );

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
              {visible.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-b-0 hover:bg-bg-elevated/50">
                  <td className="px-3 py-2.5 text-text-secondary tabular-nums">
                    Session #{r.session_id}
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
              ))}
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
      )}
    </div>
  );
}
