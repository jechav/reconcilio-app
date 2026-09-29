import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { AcceptInvite } from "./pages/AcceptInvite";
import { AuditLog } from "./pages/AuditLog";
import { Categories } from "./pages/Categories";
import { Chat } from "./pages/Chat";
import { Dashboard } from "./pages/Dashboard";
import { Export } from "./pages/Export";
import { Login } from "./pages/Login";
import { NotFound } from "./pages/NotFound";
import { Organization } from "./pages/Organization";
import { Reconciliation } from "./pages/Reconciliation";
import { Signup } from "./pages/Signup";
import { TransactionDetail } from "./pages/TransactionDetail";
import { Upload } from "./pages/Upload";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/accept-invite" element={<AcceptInvite />} />
      <Route element={<AppShell />}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/reconciliation" element={<Reconciliation />} />
        <Route path="/transactions/:id" element={<TransactionDetail />} />
        <Route path="/categories" element={<Categories />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/export" element={<Export />} />
        <Route path="/audit-log" element={<AuditLog />} />
        <Route path="/organization" element={<Organization />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
