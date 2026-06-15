import { useEffect, useState } from "react";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { getRecoveryStatus, claimRecoveryToken } from "../api/recovery";

export function RecoveryPage() {
  const [outstanding, setOutstanding] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Pre-fill token from ?token=... query param (CLI prints a claim URL)
  const params = new URLSearchParams(window.location.search);
  const tokenFromUrl = params.get("token") ?? "";

  const [token, setToken] = useState(tokenFromUrl);
  const [submitting, setSubmitting] = useState(false);
  const [claimError, setClaimError] = useState<string | null>(null);

  useEffect(() => {
    getRecoveryStatus()
      .then((s) => setOutstanding(s.outstanding))
      .catch(() => setLoadError("Failed to reach server. Is the app running?"));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    setSubmitting(true);
    setClaimError(null);
    try {
      await claimRecoveryToken(token.trim());
      // Recovery cookie is now set; navigate to /setup which will flip into recovery mode
      window.location.href = "/setup";
    } catch {
      setSubmitting(false);
      setClaimError("Invalid or expired token. Ask the operator for a fresh one.");
    }
  };

  // Show the form if we have a token from the URL even if outstanding=false
  // (the user is following a CLI-printed claim URL; let them try)
  const showForm = outstanding === true || tokenFromUrl.length > 0;

  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        {/* App name / logo area */}
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-text-primary">SkyNetControl</h1>
          <p className="text-sm text-text-muted mt-1">Admin recovery</p>
        </div>

        <div className="bg-bg-surface border border-border rounded-xl shadow-lg p-8">
          <h2 className="text-lg font-semibold text-text-primary mb-4">Enter recovery token</h2>

          {loadError && (
            <p className="text-sm text-danger mb-4">{loadError}</p>
          )}

          {outstanding === null && !loadError && (
            <p className="text-sm text-text-muted">Checking for outstanding tokens…</p>
          )}

          {outstanding === false && tokenFromUrl.length === 0 && (
            <p className="text-sm text-text-secondary">
              No recovery tokens have been issued. Ask the operator to run{" "}
              <code className="font-mono text-xs bg-bg-elevated px-1.5 py-0.5 rounded border border-border">
                skynetcontrol-recovery mint-admin-token
              </code>{" "}
              first.
            </p>
          )}

          {showForm && (
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <Input
                label="Recovery token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Paste the token here"
                mono
                autoFocus={tokenFromUrl.length === 0}
                required
              />

              {claimError && (
                <p className="text-sm text-danger">{claimError}</p>
              )}

              <Button type="submit" disabled={!token.trim() || submitting} loading={submitting} fullWidth>
                Submit
              </Button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
