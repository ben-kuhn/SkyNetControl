import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { register } from "../api/auth";
import { useAuth } from "../hooks/useAuth";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { ApiError } from "../types";

const CALLSIGN_PATTERN = /^[A-Z]{1,2}\d[A-Z]{1,4}$/;

export function RegisterPage() {
  const [callsign, setCallsign] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { refreshUser } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const upper = callsign.toUpperCase();
    if (!CALLSIGN_PATTERN.test(upper)) {
      setError("Invalid callsign format (e.g., WAØXYZ, KD0ABC)");
      return;
    }

    setLoading(true);
    try {
      await register(upper);
      await refreshUser();
      navigate("/pending");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError("Registration failed");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-bg-base flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-accent">Register</h1>
          <p className="text-text-muted text-sm mt-2">
            Enter your amateur radio callsign to get started.
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-bg-surface border border-border rounded-lg p-6"
        >
          <Input
            label="Callsign"
            value={callsign}
            onChange={(e) => setCallsign(e.target.value.toUpperCase())}
            placeholder="W0ABC"
            error={error || undefined}
            mono
            autoFocus
          />

          <Button
            type="submit"
            fullWidth
            loading={loading}
            className="mt-4"
          >
            Register
          </Button>
        </form>
      </div>
    </div>
  );
}
