import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getSetupStatus } from "../api/setup";
import { SetupPage } from "../pages/SetupPage";
import { Spinner } from "./Spinner";

export function SetupGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<{ setupCompleted: boolean; recoveryMode: boolean } | null>(null);

  useEffect(() => {
    getSetupStatus()
      .then((s) => setStatus({ setupCompleted: s.setup_completed, recoveryMode: s.recovery_mode }))
      .catch(() => setStatus({ setupCompleted: true, recoveryMode: false })); // fail-open: if /setup/status itself fails, fall through to the normal router so errors surface there
  }, []);

  if (status === null) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }
  if (!status.setupCompleted || status.recoveryMode) {
    return <SetupPage recoveryMode={status.recoveryMode} />;
  }
  return <>{children}</>;
}
