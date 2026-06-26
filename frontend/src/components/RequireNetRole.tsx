import { Navigate } from "react-router-dom";
import type { NetRole } from "../types";
import { useAuth } from "../hooks/useAuth";
import { useCurrentNet } from "../hooks/useCurrentNet";
import { Spinner } from "./Spinner";

const ROLE_RANK: Record<NetRole | "admin", number> = {
  viewer: 1,
  net_control: 2,
  admin: 99,
};

interface RequireNetRoleProps {
  children: React.ReactNode;
  /** Minimum role required to access this route. */
  min: NetRole;
}

/**
 * Guard a per-net page by role.
 *
 * - Unauthenticated users → redirect to /login (mirrors ProtectedRoute).
 * - Authenticated users with insufficient role → 403 page.
 * - Admin → always granted (admin rank 99 > any NetRole).
 * - Net loading → show spinner.
 */
export function RequireNetRole({ children, min }: RequireNetRoleProps) {
  const { user, loading: authLoading } = useAuth();
  const { role, loading: netLoading, error } = useCurrentNet();

  if (authLoading || netLoading) {
    return (
      <div className="min-h-screen bg-bg-base flex items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] gap-2">
        <p className="text-text-muted text-sm">Net not found or not accessible.</p>
      </div>
    );
  }

  const effectiveRole = role ?? "viewer"; // null means no membership → lowest rank
  const hasAccess = role !== null && ROLE_RANK[effectiveRole] >= ROLE_RANK[min];

  if (!hasAccess) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] gap-2">
        <h2 className="text-lg font-semibold text-text-primary">Access denied</h2>
        <p className="text-text-muted text-sm">
          You need at least <span className="font-mono">{min}</span> role to view this page.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
