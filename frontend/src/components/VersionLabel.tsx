import { useEffect, useState } from "react";
import { fetchVersion } from "../api/version";
import { useAuth } from "../hooks/useAuth";

// Tiny build-info readout under the admin's callsign. Lets the operator
// confirm "is the running binary actually the commit I just pushed?"
// without server access — see backend/version.py for the SHA source.
export function VersionLabel() {
  const { user } = useAuth();
  const [info, setInfo] = useState<{ version: string; sha: string } | null>(null);

  useEffect(() => {
    if (user?.role !== "admin") return;
    fetchVersion()
      .then((v) => setInfo({ version: v.version, sha: v.git_sha }))
      .catch(() => {});
  }, [user?.role]);

  if (user?.role !== "admin" || !info) return null;

  return (
    <div className="font-mono text-[0.625rem] text-text-muted px-2 leading-tight">
      v{info.version} · {info.sha}
    </div>
  );
}
