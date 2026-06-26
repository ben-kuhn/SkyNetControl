import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../context/ToastContext";
import { ScheduleList } from "./SchedulePage";

export function PendingPage() {
  const { user, refreshUser } = useAuth();
  const { addToast } = useToast();
  const navigate = useNavigate();

  useEffect(() => {
    const interval = setInterval(async () => {
      await refreshUser();
    }, 30000);
    return () => clearInterval(interval);
  }, [refreshUser]);

  useEffect(() => {
    if (user && !user.is_pending) {
      addToast("Your account has been approved!", "success");
      navigate("/schedule");
    }
  }, [user, addToast, navigate]);

  return (
    <div className="min-h-screen bg-bg-base p-4">
      <div className="max-w-2xl mx-auto">
        <div className="bg-bg-surface border border-border rounded-lg p-6 mb-6">
          <div className="flex items-center gap-3 mb-2">
            <div className="h-3 w-3 rounded-full bg-warning animate-pulse" />
            <h1 className="text-xl font-bold text-text-primary">
              Awaiting Approval
            </h1>
          </div>
          <p className="text-text-secondary text-sm">
            Your account{" "}
            <span className="font-mono text-accent">{user?.callsign}</span>{" "}
            is awaiting admin approval. You can view the net schedule below while
            you wait.
          </p>
        </div>

        <h2 className="text-lg font-semibold text-text-primary mb-4">
          Upcoming Sessions
        </h2>
        <ScheduleList />
      </div>
    </div>
  );
}
