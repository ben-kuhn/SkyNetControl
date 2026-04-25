import { useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../context/ToastContext";
import { updateCallsign } from "../api/auth";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { ApiError } from "../types";

const CALLSIGN_PATTERN = /^[A-Z]{1,2}\d[A-Z]{1,4}$/;

export function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();
  const [newCallsign, setNewCallsign] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (!user) return null;

  const handleCallsignChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const upper = newCallsign.toUpperCase();
    if (!CALLSIGN_PATTERN.test(upper)) {
      setError("Invalid callsign format (e.g., W0NE, KD0ABC)");
      return;
    }

    setLoading(true);
    try {
      await updateCallsign(upper);
      await refreshUser();
      setNewCallsign("");
      addToast("Callsign change request submitted", "success");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError("Request failed");
      }
    } finally {
      setLoading(false);
    }
  };

  const roleBadgeClass =
    user.role === "admin"
      ? "bg-accent/10 text-accent border-accent/25"
      : user.role === "net_control"
        ? "bg-success/10 text-success border-success/25"
        : "bg-bg-elevated text-text-muted border-border";

  return (
    <div className="max-w-lg">
      <h1 className="text-xl font-bold text-text-primary mb-6">Profile</h1>

      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <div className="font-mono text-2xl text-accent mb-1">
          {user.callsign}
        </div>
        <div className="text-text-secondary">{user.name}</div>
        {user.email && (
          <div className="text-text-muted text-sm mt-1">{user.email}</div>
        )}
        <span
          className={`inline-block mt-2 text-xs px-2 py-0.5 rounded border ${roleBadgeClass}`}
        >
          {user.role.replace("_", " ")}
        </span>
      </div>

      {/* Callsign change */}
      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <h2 className="text-lg font-semibold text-text-primary mb-4">
          Change Callsign
        </h2>

        {user.pending_callsign ? (
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-warning animate-pulse" />
            <span className="text-text-muted">Pending approval:</span>
            <span className="font-mono text-warning">
              {user.pending_callsign}
            </span>
          </div>
        ) : (
          <form onSubmit={handleCallsignChange} className="flex gap-3">
            <div className="flex-1">
              <Input
                value={newCallsign}
                onChange={(e) => setNewCallsign(e.target.value.toUpperCase())}
                placeholder="W0NEW"
                error={error || undefined}
                mono
              />
            </div>
            <Button type="submit" loading={loading} className="self-start">
              Request Change
            </Button>
          </form>
        )}
      </div>

      {/* PAT placeholder */}
      <div className="bg-bg-surface border border-border rounded-lg p-6">
        <h2 className="text-lg font-semibold text-text-primary mb-2">
          Personal Access Tokens
        </h2>
        <p className="text-text-muted text-sm">
          Token management is coming soon.
        </p>
      </div>
    </div>
  );
}
