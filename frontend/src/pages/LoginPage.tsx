import { useEffect, useState } from "react";
import { fetchProviders } from "../api/auth";
import { Button } from "../components/Button";
import { Spinner } from "../components/Spinner";
import type { Provider } from "../types";

export function LoginPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProviders()
      .then(setProviders)
      .catch(() => setError("Failed to load login providers"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-2 mb-2">
            <div className="h-2 w-2 rounded-full bg-success shadow-[0_0_6px_rgba(34,197,94,0.5)]" />
            <h1 className="text-2xl font-bold text-accent tracking-wide">
              SkyNetControl
            </h1>
          </div>
          <p className="text-text-muted text-sm">Winlink Net Management</p>
        </div>

        <div className="bg-bg-surface border border-border rounded-lg p-6">
          <h2 className="text-lg font-semibold text-text-primary mb-4 text-center">
            Sign In
          </h2>

          {loading && (
            <div className="flex justify-center py-4">
              <Spinner />
            </div>
          )}

          {error && (
            <p className="text-danger text-sm text-center mb-4">{error}</p>
          )}

          {!loading && !error && providers.length === 0 && (
            <p className="text-text-muted text-sm text-center">
              No login providers are configured. Contact the administrator.
            </p>
          )}

          <div className="flex flex-col gap-3">
            {providers.map((provider) => (
              <Button
                key={provider.name}
                variant="secondary"
                fullWidth
                onClick={() => {
                  window.location.href = `/api/auth/login/${provider.name}`;
                }}
              >
                Sign in with {provider.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
