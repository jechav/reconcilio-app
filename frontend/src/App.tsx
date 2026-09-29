import { Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { AuditLog } from "./pages/AuditLog";
import { Categories } from "./pages/Categories";
import { Chat } from "./pages/Chat";
import { Dashboard } from "./pages/Dashboard";
import { Export } from "./pages/Export";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Signup } from "./pages/Signup";
import { Upload } from "./pages/Upload";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route element={<AppShell />}>
        <Route path="/" element={<Home />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/categories" element={<Categories />} />
        <Route path="/export" element={<Export />} />
        <Route path="/audit-log" element={<AuditLog />} />
        <Route path="/chat" element={<Chat />} />
      </Route>
    </Routes>
  );
}
