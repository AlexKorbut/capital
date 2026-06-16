import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "@/store/auth";
import { AuthPage } from "@/pages/AuthPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { InputPage } from "@/pages/InputPage";
import { AdvisorPage } from "@/pages/AdvisorPage";
import { ScenariosPage } from "@/pages/ScenariosPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { PricingPage } from "@/pages/PricingPage";
import { LandingPage } from "@/pages/LandingPage";
import { LegalPage } from "@/pages/LegalPage";
import { VerifyEmailPage } from "@/pages/VerifyEmailPage";
import { ResetPasswordPage } from "@/pages/ResetPasswordPage";
import { BottomNav } from "@/components/BottomNav";
import { PwaUpdater } from "@/components/PwaUpdater";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const accessToken = useAuth((s) => s.accessToken);
  return accessToken ? <>{children}</> : <Navigate to="/auth" replace />;
}

/** Authenticated shell: page content + bottom padding for the nav bar. */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <div className="pb-20">{children}</div>
      <BottomNav />
    </RequireAuth>
  );
}

export default function App() {
  const accessToken = useAuth((s) => s.accessToken);

  return (
    <>
      <Routes>
        <Route
          path="/auth"
          element={accessToken ? <Navigate to="/" replace /> : <AuthPage />}
        />
        {/* Public routes (no auth shell): marketing, legal docs, email landings. */}
        <Route
          path="/welcome"
          element={accessToken ? <Navigate to="/" replace /> : <LandingPage />}
        />
        <Route path="/legal/:slug" element={<LegalPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route
          path="/"
          element={
            accessToken ? (
              <Shell>
                <DashboardPage />
              </Shell>
            ) : (
              <Navigate to="/welcome" replace />
            )
          }
        />
        <Route
          path="/input"
          element={
            <Shell>
              <InputPage />
            </Shell>
          }
        />
        <Route
          path="/advisor"
          element={
            <Shell>
              <AdvisorPage />
            </Shell>
          }
        />
        <Route
          path="/scenarios"
          element={
            <Shell>
              <ScenariosPage />
            </Shell>
          }
        />
        <Route
          path="/settings"
          element={
            <Shell>
              <SettingsPage />
            </Shell>
          }
        />
        <Route
          path="/pricing"
          element={
            <Shell>
              <PricingPage />
            </Shell>
          }
        />
        <Route
          path="*"
          element={<Navigate to={accessToken ? "/" : "/welcome"} replace />}
        />
      </Routes>
      <PwaUpdater />
    </>
  );
}
