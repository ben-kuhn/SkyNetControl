import { useEffect, useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../context/ToastContext";
import { fetchUsers, updateUserRole, approveCallsign, rejectCallsign } from "../api/users";
import { fetchAuditLog } from "../api/audit";
import { Spinner } from "../components/Spinner";
import type { User, UserRole, AuditEntry } from "../types";

const ROLE_OPTIONS: { value: UserRole; label: string }[] = [
  { value: "pending", label: "pending" },
  { value: "viewer", label: "viewer" },
  { value: "net_control", label: "net control" },
  { value: "admin", label: "admin" },
];

const roleBadgeClass: Record<UserRole, string> = {
  admin: "bg-accent/10 text-accent border-accent/25",
  net_control: "bg-success/10 text-success border-success/25",
  viewer: "bg-bg-elevated text-text-muted border-border",
  pending: "bg-warning/10 text-warning border-warning/25",
};

function formatAuditEntry(entry: AuditEntry): string {
  const d = entry.details;
  switch (entry.action) {
    case "user.role_changed":
      return `changed ${entry.target_callsign} role from ${d?.from} to ${d?.to}`;
    case "user.callsign_approved":
      return `approved callsign change ${d?.old} → ${d?.new}`;
    case "user.callsign_rejected":
      return `rejected callsign change ${d?.pending} for ${entry.target_callsign}`;
    case "config.updated":
      return `updated config ${d?.key}`;
    default:
      return entry.action;
  }
}

export function UsersPage() {
  const { user: currentUser } = useAuth();
  const { addToast } = useToast();
  const [users, setUsers] = useState<User[]>([]);
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("all");

  const loadData = () => {
    setLoading(true);
    setError(null);
    Promise.all([fetchUsers(), fetchAuditLog(20)])
      .then(([u, a]) => {
        setUsers(u);
        setAuditLog(a);
      })
      .catch(() => setError("Failed to load users"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  if (!currentUser) return null;

  const filteredUsers = users.filter((u) => {
    const matchesSearch = u.callsign.toLowerCase().includes(search.toLowerCase());
    const matchesRole = roleFilter === "all" || u.role === roleFilter;
    return matchesSearch && matchesRole;
  });

  const pendingCount =
    users.filter((u) => u.pending_callsign || u.role === "pending").length;

  const handleRoleChange = async (callsign: string, newRole: UserRole) => {
    try {
      await updateUserRole(callsign, newRole);
      addToast(`Role updated to ${newRole.replace(/_/g, " ")}`, "success");
      loadData();
    } catch {
      addToast("Failed to update role", "error");
    }
  };

  const handleApprove = async (callsign: string) => {
    try {
      await approveCallsign(callsign);
      addToast("Callsign change approved", "success");
      loadData();
    } catch {
      addToast("Failed to approve callsign", "error");
    }
  };

  const handleReject = async (callsign: string) => {
    try {
      await rejectCallsign(callsign);
      addToast("Callsign change rejected", "success");
      loadData();
    } catch {
      addToast("Failed to reject callsign", "error");
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-danger text-sm mb-2">{error}</p>
        <button
          onClick={loadData}
          className="text-accent text-sm hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-text-primary">Users</h1>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Search callsign..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted font-mono w-44"
          />
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
            className="bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-sm text-text-primary"
          >
            <option value="all">All roles</option>
            <option value="admin">Admin</option>
            <option value="net_control">Net Control</option>
            <option value="viewer">Viewer</option>
            <option value="pending">Pending</option>
          </select>
        </div>
      </div>

      {pendingCount > 0 && (
        <div className="bg-warning/5 border border-warning/25 rounded-lg px-4 py-3 mb-4 flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-warning animate-pulse" />
          <span className="text-warning text-sm font-medium">
            {pendingCount} pending action{pendingCount !== 1 ? "s" : ""} require
            attention
          </span>
        </div>
      )}

      <div className="bg-bg-surface border border-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th className="px-4 py-2 text-text-muted font-medium">Callsign</th>
              <th className="px-4 py-2 text-text-muted font-medium">Name</th>
              <th className="px-4 py-2 text-text-muted font-medium">Role</th>
              <th className="px-4 py-2 text-text-muted font-medium">Email</th>
              <th className="px-4 py-2 text-text-muted font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredUsers.map((u) => (
              <tr
                key={u.callsign}
                className={`border-b border-border last:border-0 ${
                  u.pending_callsign || u.role === "pending"
                    ? "bg-warning/5"
                    : ""
                }`}
              >
                <td className="px-4 py-3">
                  <span className="font-mono text-accent">{u.callsign}</span>
                  {u.pending_callsign && (
                    <div className="text-xs text-warning mt-0.5">
                      → <span className="font-mono">{u.pending_callsign}</span>
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-text-primary">{u.name}</td>
                <td className="px-4 py-3">
                  {u.callsign === currentUser.callsign ? (
                    <span
                      className={`inline-block text-xs px-2 py-0.5 rounded border ${roleBadgeClass[u.role]}`}
                    >
                      {u.role.replace(/_/g, " ")}
                    </span>
                  ) : (
                    <select
                      value={u.role}
                      onChange={(e) =>
                        handleRoleChange(u.callsign, e.target.value as UserRole)
                      }
                      className="bg-bg-elevated border border-border rounded px-2 py-0.5 text-xs text-text-primary"
                    >
                      {ROLE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="px-4 py-3 text-text-muted">{u.email || "—"}</td>
                <td className="px-4 py-3">
                  {u.pending_callsign ? (
                    <div className="flex gap-1">
                      <button
                        onClick={() => handleApprove(u.callsign)}
                        className="text-xs px-2 py-1 rounded bg-success/10 text-success border border-success/25 hover:bg-success/20"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleReject(u.callsign)}
                        className="text-xs px-2 py-1 rounded bg-danger/10 text-danger border border-danger/25 hover:bg-danger/20"
                      >
                        Reject
                      </button>
                    </div>
                  ) : (
                    <span className="text-text-muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {filteredUsers.length === 0 && (
        <p className="text-text-muted text-sm py-4 text-center">
          No users found.
        </p>
      )}

      <div className="text-text-muted text-xs mt-2">
        {filteredUsers.length} user{filteredUsers.length !== 1 ? "s" : ""}
      </div>

      {/* Audit Log Section */}
      {auditLog.length > 0 && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold text-text-primary mb-3">
            Recent Activity
          </h2>
          <div className="bg-bg-surface border border-border rounded-lg p-4">
            <div className="flex flex-col gap-1.5">
              {auditLog.map((entry) => (
                <div key={entry.id} className="text-xs text-text-muted">
                  <span className="text-text-secondary">
                    {new Date(entry.created_at).toLocaleString()}
                  </span>
                  {" · "}
                  <span className="font-mono text-accent">
                    {entry.actor_callsign}
                  </span>
                  {" "}
                  {formatAuditEntry(entry)}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
