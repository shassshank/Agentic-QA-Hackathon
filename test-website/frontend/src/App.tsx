import { Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { RequireSession } from "./components/RequireSession";
import { AccountDetailPage } from "./pages/AccountDetailPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LandingPage } from "./pages/LandingPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProfilePage } from "./pages/ProfilePage";
import { TransactionsPage } from "./pages/TransactionsPage";
import { TransferPage } from "./pages/TransferPage";
import { SessionProvider } from "./session";

export function App() {
  return (
    <SessionProvider>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route element={<RequireSession />}>
          <Route element={<AppLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/accounts/:accountId" element={<AccountDetailPage />} />
            <Route path="/transactions" element={<TransactionsPage />} />
            <Route path="/transfer" element={<TransferPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </SessionProvider>
  );
}
