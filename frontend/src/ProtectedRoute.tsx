import { Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import { Spinner } from "./components/Spinner";

interface ProtectedRouteProps {
  children: React.ReactNode;
  /** Require the user to be a global admin. */
  adminOnly?: boolean;
  allowPending?: boolean;
  pendingOnly?: boolean;
}

export function ProtectedRoute({
  children,
  adminOnly = false,
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
  // needing registration, even if they're already an admin (the first-signup case).
  const hasPlaceholderCallsign = user.callsign.startsWith("PENDING-");
  if (user.is_pending || hasPlaceholderCallsign) {
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
  if (pendingOnly && !user.is_pending && !hasPlaceholderCallsign) {
    return <Navigate to="/" replace />;
  }

  // Admin-only check
  if (adminOnly && !user.is_admin) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
