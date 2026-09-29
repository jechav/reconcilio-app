import { lazy, Suspense, type ComponentType } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";

// Pages export named components; adapt them to React.lazy's default-export contract.
function page<T extends Record<string, ComponentType>>(
  loader: () => Promise<T>,
  name: keyof T & string,
) {
  return lazy(async () => ({ default: (await loader())[name] }));
}

const Login = page(() => import("./pages/Login"), "Login");
const Signup = page(() => import("./pages/Signup"), "Signup");
const AcceptInvite = page(() => import("./pages/AcceptInvite"), "AcceptInvite");
const Dashboard = page(() => import("./pages/Dashboard"), "Dashboard");
const Upload = page(() => import("./pages/Upload"), "Upload");
const Reconciliation = page(() => import("./pages/Reconciliation"), "Reconciliation");
const TransactionDetail = page(() => import("./pages/TransactionDetail"), "TransactionDetail");
const Categories = page(() => import("./pages/Categories"), "Categories");
const Chat = page(() => import("./pages/Chat"), "Chat");
const Export = page(() => import("./pages/Export"), "Export");
const AuditLog = page(() => import("./pages/AuditLog"), "AuditLog");
const Organization = page(() => import("./pages/Organization"), "Organization");
const NotFound = page(() => import("./pages/NotFound"), "NotFound");

export function App() {
  return (
    <Suspense fallback={null}>
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
    </Suspense>
  );
}
