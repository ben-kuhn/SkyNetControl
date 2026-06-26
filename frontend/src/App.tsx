import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { ThemeProvider } from "./context/ThemeContext";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ToastContainer } from "./components/Toast";
import { AppShell } from "./layouts/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { PendingPage } from "./pages/PendingPage";
import { SchedulePage } from "./pages/SchedulePage";
import { ProfilePage } from "./pages/ProfilePage";
import { UsersPage } from "./pages/UsersPage";
import { ConfigPage } from "./pages/ConfigPage";
import { CheckInsPage } from "./pages/CheckInsPage";
import { MembersPage } from "./pages/MembersPage";
import { RemindersPage } from "./pages/RemindersPage";
import { RosterPage } from "./pages/RosterPage";
import { PrivacyPolicyPage } from "./pages/PrivacyPolicyPage";
import { ActivitiesPage } from "./pages/ActivitiesPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RecoveryPage } from "./pages/RecoveryPage";
import { NetsAdminPage } from "./pages/NetsAdminPage";
import { NetSettingsPage } from "./pages/NetSettingsPage";
import { NoNetsPage } from "./pages/NoNetsPage";
import { ProtectedRoute } from "./ProtectedRoute";
import { RequireNetRole } from "./components/RequireNetRole";
import { SetupGate } from "./components/SetupGate";
import { CurrentNetProvider } from "./context/CurrentNetContext";
import { useAuth } from "./hooks/useAuth";

/** Resolves the best slug for a slug-less redirect:
 *  1. localStorage["lastNetSlug"] if user has access to it
 *  2. user.nets[0]
 *  3. For admins: need to fetch via /api/nets — but we use what the auth context has
 *  4. <NoNetsPage> for auth users with no nets
 *  5. /login for anon
 */
function SlugRedirect({ to }: { to: string }) {
  const { user, loading } = useAuth();
  const navigate = useNavigate();

  if (loading) return null;

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // Try to restore last-visited slug if the user still has access
  const lastSlug = localStorage.getItem("lastNetSlug");
  const hasLastSlug = lastSlug && (
    user.is_admin || user.nets.some((n) => n.slug === lastSlug)
  );

  if (hasLastSlug && lastSlug) {
    return <Navigate to={`/nets/${lastSlug}/${to}`} replace />;
  }

  // Fall back to first membership
  const firstNet = user.nets[0];
  if (firstNet) {
    return <Navigate to={`/nets/${firstNet.slug}/${to}`} replace />;
  }

  // Admin with no explicit memberships — they can still use /nets admin page
  if (user.is_admin) {
    return <Navigate to="/nets" replace />;
  }

  // Auth user with no nets
  void navigate; // suppress unused import warning
  return <NoNetsPage />;
}

// Routes wrapped by SetupGate (all normal app routes)
function GatedRoutes() {
  return (
    <SetupGate>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          path="/register"
          element={
            <ProtectedRoute allowPending pendingOnly>
              <RegisterPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/pending"
          element={
            <ProtectedRoute allowPending>
              <PendingPage />
            </ProtectedRoute>
          }
        />

        {/* Global (non-net-scoped) authenticated routes */}
        <Route
          element={
            <ProtectedRoute>
              <AppShell />
            </ProtectedRoute>
          }
        >
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/users" element={<ProtectedRoute adminOnly><UsersPage /></ProtectedRoute>} />
          <Route path="/config" element={<ProtectedRoute adminOnly><ConfigPage /></ProtectedRoute>} />
          <Route path="/nets" element={<ProtectedRoute adminOnly><NetsAdminPage /></ProtectedRoute>} />
          <Route path="/privacy" element={<PrivacyPolicyPage />} />
        </Route>

        {/* Per-net routes: /nets/:slug/... */}
        <Route
          path="/nets/:slug/*"
          element={
            <ProtectedRoute>
              <CurrentNetProvider>
                <AppShell />
              </CurrentNetProvider>
            </ProtectedRoute>
          }
        >
          <Route path="schedule" element={<RequireNetRole min="viewer"><SchedulePage /></RequireNetRole>} />
          <Route path="checkins" element={<CheckInsPage />} />
          <Route path="members" element={<RequireNetRole min="viewer"><MembersPage /></RequireNetRole>} />
          <Route path="reminders" element={<RequireNetRole min="net_control"><RemindersPage /></RequireNetRole>} />
          <Route path="roster" element={<RequireNetRole min="net_control"><RosterPage /></RequireNetRole>} />
          <Route path="activities" element={<RequireNetRole min="net_control"><ActivitiesPage /></RequireNetRole>} />
          <Route path="settings" element={<RequireNetRole min="net_control"><NetSettingsPage /></RequireNetRole>} />
        </Route>

        {/* Slug-less aliases — redirect to /nets/:slug/<page> */}
        <Route path="/schedule" element={<SlugRedirect to="schedule" />} />
        <Route path="/checkins" element={<SlugRedirect to="checkins" />} />
        <Route path="/members" element={<SlugRedirect to="members" />} />
        <Route path="/reminders" element={<SlugRedirect to="reminders" />} />
        <Route path="/roster" element={<SlugRedirect to="roster" />} />
        <Route path="/activities" element={<SlugRedirect to="activities" />} />

        <Route path="/" element={<SlugRedirect to="schedule" />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </SetupGate>
  );
}

function AppRoutes() {
  return (
    <Routes>
      {/* Recovery page is outside SetupGate so it's always reachable, even
          when recovery_mode=true would cause SetupGate to redirect elsewhere. */}
      <Route path="/recovery" element={<RecoveryPage />} />

      {/* All other routes go through SetupGate */}
      <Route path="*" element={<GatedRoutes />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <ToastProvider>
            <AppRoutes />
            <ToastContainer />
          </ToastProvider>
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
