import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getSetupStatus } from "../api/setup";
import { SetupPage } from "../pages/SetupPage";
import { Spinner } from "./Spinner";

export function SetupGate({ children }: { children: ReactNode }) {
  const [setupCompleted, setSetupCompleted] = useState<boolean | null>(null);

  useEffect(() => {
    getSetupStatus()
      .then((s) => setSetupCompleted(s.setup_completed))
      .catch(() => setSetupCompleted(true)); // fail-open: if /setup/status itself fails, fall through to the normal router so errors surface there
  }, []);

  if (setupCompleted === null) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }
  if (!setupCompleted) {
    return <SetupPage />;
  }
  return <>{children}</>;
}
