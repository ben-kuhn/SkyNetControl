import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
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
import { ProtectedRoute } from "./ProtectedRoute";
import { SetupGate } from "./components/SetupGate";
import type { UserRole } from "./types";

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

        {/* Public routes that share the AppShell chrome */}
        <Route element={<AppShell />}>
          <Route path="/checkins" element={<CheckInsPage />} />
        </Route>

        {/* Authenticated routes */}
        <Route
          element={
            <ProtectedRoute>
              <AppShell />
            </ProtectedRoute>
          }
        >
          <Route path="/schedule" element={<SchedulePage />} />
          <Route
            path="/profile"
            element={
              <ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}>
                <ProfilePage />
              </ProtectedRoute>
            }
          />
          <Route path="/members" element={<ProtectedRoute minRole={["viewer", "net_control", "admin"] as UserRole[]}><MembersPage /></ProtectedRoute>} />
          <Route path="/reminders" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><RemindersPage /></ProtectedRoute>} />
          <Route path="/roster" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><RosterPage /></ProtectedRoute>} />
          <Route path="/activities" element={<ProtectedRoute minRole={["net_control", "admin"] as UserRole[]}><ActivitiesPage /></ProtectedRoute>} />
          <Route path="/users" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><UsersPage /></ProtectedRoute>} />
          <Route path="/config" element={<ProtectedRoute minRole={["admin"] as UserRole[]}><ConfigPage /></ProtectedRoute>} />
          <Route path="/privacy" element={<PrivacyPolicyPage />} />
        </Route>

        <Route path="/" element={<Navigate to="/schedule" replace />} />
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
