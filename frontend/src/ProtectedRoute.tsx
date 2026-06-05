import { Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import { Spinner } from "./components/Spinner";
import type { UserRole } from "./types";

interface ProtectedRouteProps {
  children: React.ReactNode;
  minRole?: UserRole[];
  allowPending?: boolean;
  pendingOnly?: boolean;
}

export function ProtectedRoute({
  children,
  minRole,
  allowPending = false,
  pendingOnly = false,
}: ProtectedRouteProps) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-bg-base flex items-center justify-center">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // Treat anyone still carrying a PENDING-... placeholder callsign as
  // needing registration, even if their role is already ADMIN (the
  // first-signup case).
  const hasPlaceholderCallsign = user.callsign.startsWith("PENDING-");
  if (user.role === "pending" || hasPlaceholderCallsign) {
    if (pendingOnly && !hasPlaceholderCallsign) {
      return <Navigate to="/pending" replace />;
    }
    if (!allowPending) {
      if (hasPlaceholderCallsign) {
        return <Navigate to="/register" replace />;
      }
      return <Navigate to="/pending" replace />;
    }
  }

  // Non-pending user trying to access pending-only routes
  if (pendingOnly && user.role !== "pending") {
    return <Navigate to="/schedule" replace />;
  }

  // Role check
  if (minRole && !minRole.includes(user.role)) {
    return <Navigate to="/schedule" replace />;
  }

  return <>{children}</>;
}
