import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../context/ToastContext";
import { updateCallsign } from "../api/auth";
import { createToken, listTokens, revokeToken } from "../api/tokens";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Modal } from "../components/Modal";
import { Spinner } from "../components/Spinner";
import { ApiError, SCOPES } from "../types";
import type { ScopeMinRole, Token } from "../types";
import { exportMyData, anonymizeMyAccount } from "../api/privacy";

const CALLSIGN_PATTERN = /^[A-Z]{1,2}\d[A-Z]{1,4}$/;

// Local rank map for PAT scope UI — admin rank is higher than net_control.
// "deleted"/"pending" are no longer user-visible roles; we represent
// user state via is_admin/is_pending flags on the User object instead.
const SCOPE_ROLE_RANK: Record<ScopeMinRole, number> = {
  viewer: 1,
  net_control: 2,
  admin: 3,
};

/** Determine if the current user can select a PAT scope based on their access level. */
function canUseScope(effectiveRole: ScopeMinRole, minRole: ScopeMinRole): boolean {
  return SCOPE_ROLE_RANK[effectiveRole] >= SCOPE_ROLE_RANK[minRole];
}

export function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();

  // Callsign change state
  const [newCallsign, setNewCallsign] = useState("");
  const [callsignError, setCallsignError] = useState<string | null>(null);
  const [callsignLoading, setCallsignLoading] = useState(false);

  // Token state
  const [tokens, setTokens] = useState<Token[]>([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [tokenName, setTokenName] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [tokenExpiry, setTokenExpiry] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [createLoading, setCreateLoading] = useState(false);
  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<Token | null>(null);
  const [revokeLoading, setRevokeLoading] = useState(false);

  // Privacy state
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);

  const loadTokens = useCallback(async () => {
    try {
      const data = await listTokens();
      setTokens(data);
    } catch {
      addToast("Failed to load tokens", "error");
    } finally {
      setTokensLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    loadTokens();
  }, [loadTokens]);

  if (!user) return null;

  const handleCallsignChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setCallsignError(null);
    const upper = newCallsign.toUpperCase();
    if (!CALLSIGN_PATTERN.test(upper)) {
      setCallsignError("Invalid callsign format (e.g., WAØXYZ, KD0ABC)");
      return;
    }
    setCallsignLoading(true);
    try {
      await updateCallsign(upper);
      await refreshUser();
      setNewCallsign("");
      addToast("Callsign change request submitted", "success");
    } catch (err) {
      setCallsignError(err instanceof ApiError ? err.detail : "Request failed");
    } finally {
      setCallsignLoading(false);
    }
  };

  const handleCreateToken = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    if (!tokenName.trim()) {
      setCreateError("Name is required");
      return;
    }
    if (selectedScopes.length === 0) {
      setCreateError("Select at least one scope");
      return;
    }
    setCreateLoading(true);
    try {
      const result = await createToken({
        name: tokenName.trim(),
        scopes: selectedScopes,
        expires_at: tokenExpiry || undefined,
      });
      setRevealedToken(result.token);
      setTokenName("");
      setSelectedScopes([]);
      setTokenExpiry("");
      setShowCreateForm(false);
      await loadTokens();
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.detail : "Failed to create token");
    } finally {
      setCreateLoading(false);
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    setRevokeLoading(true);
    try {
      await revokeToken(revokeTarget.id);
      addToast("Token revoked", "success");
      setRevokeTarget(null);
      await loadTokens();
    } catch {
      addToast("Failed to revoke token", "error");
    } finally {
      setRevokeLoading(false);
    }
  };

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  };

  const handleExportData = async () => {
    try {
      await exportMyData();
      addToast("Data export downloaded", "success");
    } catch {
      addToast("Failed to export data", "error");
    }
  };

  const handleDeleteAccount = async () => {
    if (deleteConfirm !== "DELETE") return;
    setDeleting(true);
    try {
      await anonymizeMyAccount();
      window.location.href = "/login";
    } catch {
      addToast("Failed to delete account", "error");
      setDeleting(false);
    }
  };

  const copyToken = async () => {
    if (revealedToken) {
      await navigator.clipboard.writeText(revealedToken);
      addToast("Token copied to clipboard", "success");
    }
  };

  // Derive display role from flags
  const roleBadgeClass = user.is_admin
    ? "bg-accent/10 text-accent border-accent/25"
    : user.is_pending
      ? "bg-warning/10 text-warning border-warning/25"
      : user.nets.some((n) => n.role === "net_control")
        ? "bg-success/10 text-success border-success/25"
        : "bg-bg-elevated text-text-muted border-border";

  const roleLabel = user.is_admin
    ? "admin"
    : user.is_pending
      ? "pending"
      : user.nets.some((n) => n.role === "net_control")
        ? "net control"
        : "member";

  return (
    <div className="max-w-lg">
      <h1 className="text-xl font-bold text-text-primary mb-6">Profile</h1>

      {/* User info */}
      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <div className="font-mono text-2xl text-accent mb-1">{user.callsign}</div>
        <div className="text-text-secondary">{user.name}</div>
        {user.email && <div className="text-text-muted text-sm mt-1">{user.email}</div>}
        <span className={`inline-block mt-2 text-xs px-2 py-0.5 rounded border ${roleBadgeClass}`}>
          {roleLabel}
        </span>
      </div>

      {/* Callsign change */}
      <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
        <h2 className="text-lg font-semibold text-text-primary mb-4">Change Callsign</h2>
        {user.pending_callsign ? (
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-warning animate-pulse" />
            <span className="text-text-muted">Pending approval:</span>
            <span className="font-mono text-warning">{user.pending_callsign}</span>
          </div>
        ) : (
          <form onSubmit={handleCallsignChange} className="flex gap-3">
            <div className="flex-1">
              <Input
                value={newCallsign}
                onChange={(e) => setNewCallsign(e.target.value.toUpperCase())}
                placeholder="WAØXYZ"
                error={callsignError || undefined}
                mono
              />
            </div>
            <Button type="submit" loading={callsignLoading} className="self-start">
              Request Change
            </Button>
          </form>
        )}
      </div>

      {/* Personal Access Tokens */}
      <div className="bg-bg-surface border border-border rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-text-primary">Personal Access Tokens</h2>
          {!showCreateForm && !revealedToken && (
            <Button size="sm" onClick={() => setShowCreateForm(true)}>
              Create Token
            </Button>
          )}
        </div>

        {/* Token reveal */}
        {revealedToken && (
          <div className="mb-4 p-4 bg-warning/10 border border-warning/25 rounded-lg">
            <p className="text-sm text-warning font-semibold mb-2">
              Copy this token now. It will not be shown again.
            </p>
            <div className="flex items-center gap-2 mb-3">
              <code className="flex-1 font-mono text-xs bg-bg-base p-2 rounded border border-border break-all text-text-primary">
                {revealedToken}
              </code>
              <Button size="sm" onClick={copyToken}>
                Copy
              </Button>
            </div>
            <Button size="sm" variant="secondary" onClick={() => setRevealedToken(null)}>
              Done
            </Button>
          </div>
        )}

        {/* Create form */}
        {showCreateForm && (
          <form onSubmit={handleCreateToken} className="mb-4 p-4 bg-bg-base rounded-lg border border-border">
            <Input
              label="Token name"
              value={tokenName}
              onChange={(e) => setTokenName(e.target.value)}
              placeholder="OpenClaw integration"
              error={createError || undefined}
              autoFocus
            />
            <div className="mt-3">
              <label className="block text-sm text-text-secondary mb-2">Scopes</label>
              <div className="space-y-1">
                {Object.entries(SCOPES).map(([scope, { description, minRole }]) => {
                  // Effective scope role: admins get full rank; net_control from any net;
                  // otherwise viewer. Task 15 will refine this to be per-net-bound token.
                  const effectiveRole: ScopeMinRole = user.is_admin
                    ? "admin"
                    : user.nets.some((n) => n.role === "net_control")
                      ? "net_control"
                      : "viewer";
                  const allowed = canUseScope(effectiveRole, minRole);
                  return (
                    <label
                      key={scope}
                      className={`flex items-center gap-2 text-sm ${allowed ? "text-text-secondary" : "text-text-muted opacity-50"}`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedScopes.includes(scope)}
                        onChange={() => toggleScope(scope)}
                        disabled={!allowed}
                        className="accent-accent"
                      />
                      <span className="font-mono text-xs">{scope}</span>
                      <span className="text-text-muted">— {description}</span>
                    </label>
                  );
                })}
              </div>
            </div>
            <div className="mt-3">
              <Input
                label="Expiry (optional)"
                type="datetime-local"
                value={tokenExpiry}
                onChange={(e) => setTokenExpiry(e.target.value)}
              />
            </div>
            <div className="flex gap-2 mt-4">
              <Button type="submit" loading={createLoading}>
                Create
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setShowCreateForm(false);
                  setCreateError(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        )}

        {/* Token list */}
        {tokensLoading ? (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        ) : tokens.length === 0 && !revealedToken ? (
          <p className="text-text-muted text-sm">No tokens created yet.</p>
        ) : (
          <div className="space-y-3">
            {tokens.map((t) => (
              <div
                key={t.id}
                className="flex items-start justify-between p-3 bg-bg-base rounded-lg border border-border"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium text-text-primary">{t.name}</div>
                  <div className="font-mono text-xs text-text-muted mt-0.5">{t.token_prefix}...</div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {t.scopes.map((s) => (
                      <span
                        key={s}
                        className="text-xs px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/25"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                  <div className="text-xs text-text-muted mt-1">
                    Created {new Date(t.created_at).toLocaleDateString()}
                    {t.last_used_at && ` · Last used ${new Date(t.last_used_at).toLocaleDateString()}`}
                    {t.expires_at && (
                      <span className={t.is_expired ? "text-danger" : ""}>
                        {" · "}
                        {t.is_expired ? "Expired" : `Expires ${new Date(t.expires_at).toLocaleDateString()}`}
                      </span>
                    )}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => setRevokeTarget(t)}
                >
                  Revoke
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Revoke confirmation modal */}
      <Modal
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revoke Token"
      >
        <p className="text-text-secondary text-sm mb-4">
          Revoke token &ldquo;{revokeTarget?.name}&rdquo;? Any integrations using this token will stop working immediately.
        </p>
        <div className="flex gap-2 justify-end">
          <Button variant="secondary" onClick={() => setRevokeTarget(null)}>
            Cancel
          </Button>
          <Button variant="danger" loading={revokeLoading} onClick={handleRevoke}>
            Revoke
          </Button>
        </div>
      </Modal>

      {/* Privacy & Data Section */}
      <div className="mt-8">
        <h2 className="text-lg font-semibold text-text-primary mb-3">
          Privacy & Data
        </h2>
        <div className="bg-bg-surface border border-border rounded-lg p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-text-primary font-medium">
                Download My Data
              </p>
              <p className="text-xs text-text-muted">
                Export all your data as a JSON file
              </p>
            </div>
            <button
              onClick={handleExportData}
              className="text-xs px-3 py-1.5 rounded bg-accent/10 text-accent border border-accent/25 hover:bg-accent/20"
            >
              Download
            </button>
          </div>
          <div className="border-t border-border" />
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-text-primary font-medium">
                Delete My Account
              </p>
              <p className="text-xs text-text-muted">
                Permanently anonymize your account and all personal data
              </p>
            </div>
            <button
              onClick={() => setShowDeleteDialog(true)}
              className="text-xs px-3 py-1.5 rounded bg-danger/10 text-danger border border-danger/25 hover:bg-danger/20"
            >
              Delete Account
            </button>
          </div>
        </div>
      </div>

      {/* Delete confirmation dialog */}
      {showDeleteDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-bg-surface border border-border rounded-lg p-6 max-w-md mx-4">
            <h3 className="text-lg font-bold text-danger mb-2">
              Delete Account
            </h3>
            <p className="text-sm text-text-secondary mb-3">
              This action is <strong>irreversible</strong>. Your account will be
              anonymized and all personal data replaced with placeholders. You
              will be logged out immediately.
            </p>
            <p className="text-sm text-text-secondary mb-3">
              Type <strong>DELETE</strong> to confirm:
            </p>
            <input
              type="text"
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              className="w-full bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-sm text-text-primary font-mono mb-4"
              placeholder="Type DELETE"
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => {
                  setShowDeleteDialog(false);
                  setDeleteConfirm("");
                }}
                className="text-xs px-3 py-1.5 rounded bg-bg-elevated text-text-muted border border-border hover:bg-bg-base"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteAccount}
                disabled={deleteConfirm !== "DELETE" || deleting}
                className="text-xs px-3 py-1.5 rounded bg-danger text-white border border-danger hover:bg-danger/90 disabled:opacity-50"
              >
                {deleting ? "Deleting..." : "Confirm Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
