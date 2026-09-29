import {
  ArrowLeftRight,
  Download,
  History,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  Upload,
  type LucideIcon,
} from "lucide-react";
import { Link, NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";

import { clearSession, getSession } from "@/session";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/upload", label: "Upload", icon: Upload },
  { to: "/chat", label: "Ask your books", icon: MessageSquare },
  { to: "/export", label: "Export", icon: Download },
  { to: "/audit-log", label: "Audit log", icon: History },
];

function initials(email: string): string {
  return email.slice(0, 2).toUpperCase();
}

export function AppShell() {
  const session = getSession();
  const navigate = useNavigate();

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  function handleLogout() {
    clearSession();
    navigate("/login");
  }

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col justify-between bg-navy-600 px-4 py-6">
        <div className="flex flex-col gap-8">
          <Link to="/" className="flex items-center gap-2.5 px-2">
            <span className="flex size-7 items-center justify-center rounded-lg bg-white text-navy-600">
              <ArrowLeftRight className="size-4" aria-hidden />
            </span>
            <span className="text-lg font-bold tracking-tight text-white">Reconcilio</span>
          </Link>
          <nav aria-label="Main" className="flex flex-col gap-1">
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-[#c9d8e6] transition-colors hover:bg-white/10",
                    isActive && "bg-white/15 font-semibold text-white",
                  )
                }
              >
                <Icon className="size-[18px] shrink-0" aria-hidden />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3 rounded-lg bg-white/10 p-3">
          <span
            aria-hidden
            className="flex size-8 shrink-0 items-center justify-center rounded-full bg-navy-100 text-xs font-bold text-navy-600"
          >
            {initials(session.user.email)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-semibold leading-4 text-white">
              {session.organization.name}
            </p>
            <p className="truncate text-xs leading-4 text-[#c9d8e6] capitalize">{session.role}</p>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            aria-label="Log out"
            className="rounded-md p-1.5 text-[#c9d8e6] hover:bg-white/10 hover:text-white"
          >
            <LogOut className="size-4" aria-hidden />
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-10 py-8">
        <Outlet />
      </main>
    </div>
  );
}
