import { Link, Route, Routes } from "react-router-dom";

import { AuditLog } from "./pages/AuditLog";
import { Dashboard } from "./pages/Dashboard";
import { Export } from "./pages/Export";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Signup } from "./pages/Signup";
import { Upload } from "./pages/Upload";

export function App() {
  return (
    <div>
      <nav>
        <Link to="/">Reconcilio</Link>
        <Link to="/login">Log in</Link>
        <Link to="/signup">Sign up</Link>
        <Link to="/upload">Upload</Link>
        <Link to="/dashboard">Dashboard</Link>
        <Link to="/export">Export</Link>
        <Link to="/audit-log">Audit log</Link>
      </nav>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/export" element={<Export />} />
        <Route path="/audit-log" element={<AuditLog />} />
      </Routes>
    </div>
  );
}
